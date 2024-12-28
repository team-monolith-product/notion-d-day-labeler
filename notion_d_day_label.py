"""
프로젝트: Notion 문서로부터 D-Day 라벨

GitHub Pull Request 생성 이벤트가 발생할 때,
1) PR 제목에서 노션 작업 ID를 추출하고 
2) 해당 노션 작업 ID에 해당하는 노션 페이지를 가져와서,
3) 노션 페이지의 마감 기한을 추출하고
4) 해당 마감 기한을 기반으로 D-Day 라벨을 생성하는 것을 목표로 합니다.

PR 이벤트가 아닌 경우 레포지토리의 모든 PR을 대상으로 D-Day 라벨을 갱신합니다.
"""

from datetime import datetime
import os
import re
from zoneinfo import ZoneInfo

import dotenv

from github import Github, UnknownObjectException
from github.PullRequest import PullRequest

from notion_client import Client as NotionClient

dotenv.load_dotenv()


def main():
    # 0) Load environment variables
    event_name = os.getenv("GITHUB_EVENT_NAME")
    github_token = os.getenv("GITHUB_TOKEN")
    repo_name = os.getenv("GITHUB_REPOSITORY")  # "owner/repo"
    pr_number_str = os.getenv("PR_NUMBER")      # e.g. "123"
    notion_token = os.getenv("NOTION_TOKEN")

    if not event_name or not github_token or not repo_name or not notion_token:
        raise EnvironmentError(
            "Missing one or more required environment variables: "
            "GITHUB_EVENT_NAME, GITHUB_TOKEN, GITHUB_REPOSITORY, NOTION_TOKEN."
        )

    g = Github(github_token)
    notion = NotionClient(auth=notion_token)
    repo = g.get_repo(repo_name)

    if event_name == "pull_request":
        if not pr_number_str:
            raise EnvironmentError("Missing environment variable: PR_NUMBER.")
        pr_number = int(pr_number_str)
        pr = repo.get_pull(pr_number)
        update_d_day_label_for_pr(notion, pr)
    elif event_name == "schedule" or event_name == "workflow_dispatch":
        pulls = repo.get_pulls(state="open")
        for pr in pulls:
            update_d_day_label_for_pr(notion, pr)
    else:
        print("This script only runs on pull_request or schedule events.")


def update_d_day_label_for_pr(
    notion: NotionClient,
    pr: PullRequest,
):
    """
    PR에 D-Day 라벨을 추가하거나 갱신합니다.
    노션 페이지의 마감 기한을 기준으로 D-Day 라벨 계산 후 적용합니다.

    Args:
        notion (NotionClient)
        pr (PullRequest)
    """
    title = pr.title

    # 1) Extract Notion task ID from PR title
    db_name_prefixes = extract_notion_db_name_prefixes(notion)

    # 2) Extract Task ID from PR title
    task_id = extract_dynamic_task_id(
        title, [prefix["prefix"] for prefix in db_name_prefixes])
    if task_id:
        print(f"Extracted Task ID: {task_id}")
    else:
        print(f"No valid Notion Task ID found in the PR title '{title}'.")
        return

    prefix = task_id.split("-")[0]
    number = int(task_id.split("-")[1])
    database_id, property_name = next(
        (db_name_prefix["database_id"], db_name_prefix["property_name"])
        for db_name_prefix in db_name_prefixes if db_name_prefix["prefix"].lower() == prefix.lower()
    )

    notion_page = search_page(notion, database_id, property_name, number)
    if notion_page:
        print(f"Fetched Notion Page ID: {notion_page['id']}")
    else:
        print(f"No Notion page found for Task ID: {task_id}")
        return

    # 3) Fetch Notion page date
    field_date_value = notion_page.get("properties", {}).get("타임라인", {}).get("date", {})
    notion_page_date_str = field_date_value.get("end") or field_date_value.get("start")

    d_day_label = calculate_d_day_label(notion_page_date_str)

    # 6) Add label to PR
    repo = pr.base.repo
    color_map = {
        "D-0": "ED1C24",  # 빨강
        "D-1": "F08650",  # 주황
        "D-2": "FFFD55",  # 노랑
    }

    # 이미 존재하는 D-Day 라벨들을 제거합니다.
    existing_labels = [
        lbl for lbl in pr.get_labels() if lbl.name.startswith("D-")]
    for lbl in existing_labels:
        pr.remove_from_labels(lbl)

    if d_day_label:
        color = color_map.get(d_day_label, "75F94D")  # 초록
        try:
            label_obj = repo.get_label(d_day_label)
            print(f"Reusing existing label: {d_day_label}")
        except UnknownObjectException:
            # Label does not exist; create it
            label_obj = repo.create_label(
                name=d_day_label,
                color=color,
                description="D-Day Label"
            )
            print(f"Created new label: {d_day_label}")
        pr.add_to_labels(label_obj)

        print(f"Label {d_day_label} is successfully added to PR #{pr.number}.")


def calculate_d_day_label(due_date_str: str | None) -> str | None:
    """
    Notion의 마감기한(ISO8601 문자열)을 받아서 남은 일수에 따라 라벨을 결정합니다.
    """
    if not due_date_str:
        # due_date가 없으면 라벨을 달지 않음
        return None

    try:
        due_date = datetime.fromisoformat(due_date_str)
    except ValueError:
        return None

    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    # due_date도 timezone이 UTC가 아닐 수 있으니 utc로 변환
    due_date_kst = due_date.replace(tzinfo=ZoneInfo("Asia/Seoul"))

    day_diff = (due_date_kst.date() - now_kst.date()).days

    if day_diff <= 0:
        return "D-0"
    else:
        return f"D-{day_diff}"


def extract_notion_db_name_prefixes(notion: NotionClient) -> list[dict]:
    """
    연결된 노션 계정의 모든 데이터베이스에서
    Unique ID 속성의 접두사를 추출합니다.

    Args:
        notion (NotionClient)

    Returns:
        [{
            "prefix": "TASK",
            "database_id": "12345678-1234-1234-1234-1234567890ab",
            "property_name": "ID"
        }]
    """
    databases = notion.search(
        filter={
            "value": "database",
            "property": "object"
        }
    )["results"]

    # select a property which type is unique_id
    return [
        {
            "prefix": property["unique_id"]["prefix"],
            "database_id": db["id"],
            "property_name": property["name"]
        }
        for db in databases
        for property in db["properties"].values()
        if property["type"] == "unique_id"
    ]


def extract_dynamic_task_id(title: str, prefixes: list[str]) -> str | None:
    """
    PR 제목에서 동적으로 Task ID를 추출합니다.

    Args:
        title (str): PR 제목
        prefixes (List[str]): 데이터베이스 접두사의 리스트

    Returns:
        추출된 Task ID (예: 'TASK-1234') 또는 None
    """
    # 접두사를 포함한 정규식을 동적으로 생성
    pattern = r"(" + "|".join(re.escape(prefix)
                              for prefix in prefixes) + r")[\-\s](\d+)"
    match = re.search(pattern, title, re.IGNORECASE)
    if match:
        return f"{match.group(1).upper()}-{match.group(2)}"  # 예: TASK-1234
    return None


def search_page(
    notion: NotionClient,
    database_id: str,
    property_name: str,
    number: int
) -> dict | None:
    """
    노션 페이지를 검색해옵니다.

    Args:
        notion (NotionClient)
        database_id (str): 노션 데이터베이스 ID
        property_name (str): Task ID를 저장하는 속성 이름
        number (int): 노션 페이지의 Task ID

    Returns:
        노션 페이지의 정보 또는 None
    """
    response = notion.databases.query(
        database_id=database_id,
        filter={
            "property": property_name,
            "unique_id": {
                "equals": number
            }
        }
    )

    results = response.get("results", [])
    if not results:
        return None
    return results[0]  # 첫 번째 매칭된 페이지 반환


if __name__ == "__main__":
    main()
