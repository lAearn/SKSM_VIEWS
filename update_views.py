
import os
import re
import requests
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


# ==========================================
# 1. API 인증 정보 (GitHub Secrets)
# ==========================================

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

if not all([YOUTUBE_API_KEY, NOTION_TOKEN, NOTION_DATABASE_ID]):
    raise ValueError(
        "GitHub Secrets에 YOUTUBE_API_KEY, "
        "NOTION_TOKEN, NOTION_DATABASE_ID가 "
        "모두 등록되어 있는지 확인하세요."
    )

NOTION_VERSION = "2022-06-28"
KST = ZoneInfo("Asia/Seoul")

notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json"
}


# ==========================================
# 2. 노션 데이터베이스 속성 확인
# ==========================================

def get_database_schema():
    response = requests.get(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}",
        headers=notion_headers,
        timeout=30
    )
    response.raise_for_status()
    return response.json().get("properties", {})


def check_database_schema():
    schema = get_database_schema()

    print("노션 데이터베이스 속성 확인:")

    for name, prop in schema.items():
        print(f"  {name}: {prop.get('type')}")

    if "조회수" not in schema or schema["조회수"].get("type") != "number":
        raise ValueError(
            "노션 데이터베이스에 '조회수' 숫자 속성이 없습니다."
        )

    if "영상링크" not in schema or schema["영상링크"].get("type") != "url":
        raise ValueError(
            "노션 데이터베이스에 '영상링크' URL 속성이 없습니다."
        )

    if "마지막 갱신" not in schema or schema["마지막 갱신"].get("type") != "date":
        raise ValueError(
            "노션 데이터베이스에 '마지막 갱신' 날짜 속성이 없습니다."
        )

    print("데이터베이스 속성 확인 완료!")


def mark_updated(page_id):
    now = datetime.now(KST).isoformat()

    response = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=notion_headers,
        json={
            "properties": {
                "마지막 갱신": {
                    "date": {"start": now}
                }
            }
        },
        timeout=(10, 20)
    )

    response.raise_for_status()


# ==========================================
# 3. 노션 데이터베이스의 모든 페이지 가져오기
# ==========================================

def get_notion_pages():
    pages = []
    cursor = None

    while True:
        payload = {"page_size": 100}

        if cursor:
            payload["start_cursor"] = cursor

        response = requests.post(
            f"https://api.notion.com/v1/databases/"
            f"{NOTION_DATABASE_ID}/query",
            headers=notion_headers,
            json=payload,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()
        pages.extend(data.get("results", []))

        if not data.get("has_more"):
            break

        cursor = data.get("next_cursor")

    return pages


# ==========================================
# 4. 유튜브 링크에서 영상 ID 추출
# ==========================================

def extract_video_id(url):
    if not url:
        return None

    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().split(":")[0]

        if host.startswith("www."):
            host = host[4:]

        if host == "youtu.be":
            video_id = parsed.path.strip("/").split("/")[0]

        elif host in (
            "youtube.com",
            "m.youtube.com",
            "music.youtube.com"
        ):
            if parsed.path == "/watch":
                video_id = parse_qs(parsed.query).get("v", [None])[0]

            else:
                match = re.match(
                    r"^/(?:shorts|live|embed)/([^/?]+)",
                    parsed.path
                )

                video_id = match.group(1) if match else None

        else:
            return None

        if video_id and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return video_id

    except Exception:
        pass

    return None


# ==========================================
# 5. 유튜브 API로 최신 조회수 가져오기
# ==========================================

def get_youtube_views(video_ids):
    views = {}
    unavailable_ids = set()

    # 한 번의 API 요청에 최대 50개 영상
    for start in range(0, len(video_ids), 50):
        batch = video_ids[start:start + 50]

        response = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "statistics",
                "id": ",".join(batch),
                "key": YOUTUBE_API_KEY
            },
            timeout=30
        )

        response.raise_for_status()

        data = response.json()
        found_ids = set()

        for item in data.get("items", []):
            video_id = item["id"]
            found_ids.add(video_id)

            statistics = item.get("statistics", {})

            if "viewCount" in statistics:
                views[video_id] = int(statistics["viewCount"])

        unavailable_ids.update(set(batch) - found_ids)

    return views, unavailable_ids


# ==========================================
# 6. 노션의 조회수 속성 업데이트
# ==========================================

def update_notion_views(page_id, view_count):
    response = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=notion_headers,
        json={
            "properties": {
                "조회수": {"number": view_count}
            }
        },
        timeout=(10, 20)
    )

    response.raise_for_status()



# ==========================================
# 7. 전체 갱신 실행
# ==========================================

def run_update():

    print("\n" + "=" * 45)
    print("UTC 현재 시각:", datetime.now(timezone.utc))
    print("KST 현재 시각:", datetime.now(KST))
    print("=" * 45)

    print(
        f"갱신 시작: "
        f"{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}"
    )
    print("=" * 45)

    print("\n노션 데이터베이스 속성 확인 중...")
    check_database_schema()

    print("\n노션 페이지를 불러오는 중...")
    pages = get_notion_pages()

    page_video_ids = {}
    missing_links = 0

    for page in pages:
        properties = page.get("properties", {})
        link_prop = properties.get("영상링크", {})
        url = link_prop.get("url")

        video_id = extract_video_id(url)

        if video_id:
            page_video_ids[page["id"]] = video_id
        else:
            missing_links += 1

    # 중복 영상 제거
    unique_video_ids = list(dict.fromkeys(page_video_ids.values()))

    print(f"\n노션 페이지 수: {len(pages)}")
    print(f"영상 링크 확인 성공: {len(page_video_ids)}")
    print(f"링크 누락 또는 인식 불가: {missing_links}")
    print(f"중복 제거 후 영상 수: {len(unique_video_ids)}")

    if not unique_video_ids:
        print("갱신할 영상이 없습니다.")
        return

    print("\n유튜브에서 최신 조회수를 가져오는 중...")

    latest_views, unavailable_ids = get_youtube_views(
        unique_video_ids
    )

    print(f"조회수 확인 성공: {len(latest_views)}개")
    print(f"찾지 못한 영상: {len(unavailable_ids)}개")

    updated = 0
    failed = []

    for page_id, video_id in page_video_ids.items():

        if video_id not in latest_views:
            continue

        try:
            update_notion_views(
                page_id,
                latest_views[video_id]
            )

            mark_updated(page_id)

            updated += 1

            if updated % 10 == 0:
                print(f"{updated}개 업데이트 완료")

        except Exception as e:
            failed.append((page_id, str(e)))
            print(f"업데이트 실패: {page_id} / {e}")

    print("\n========== 갱신 결과 ==========")
    print(f"업데이트 성공: {updated}개")
    print(f"조회수를 가져오지 못한 영상: {len(unavailable_ids)}개")
    print(f"링크 누락 또는 인식 불가: {missing_links}개")
    print(f"노션 업데이트 실패: {len(failed)}개")

    print(
        f"갱신 종료: "
        f"{datetime.now(KST).strftime('%H:%M:%S')}"
    )

    print("================================")


# ==========================================
# 8. 프로그램 시작
# ==========================================

if __name__ == "__main__":
    run_update()
