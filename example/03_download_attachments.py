import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fanding import FandingClient, PostNotFound

client = FandingClient(
    auth_token=os.getenv("FANDING_AUTH_TOKEN"),
    cookies=os.getenv("FANDING_COOKIE"),
)

post_ref = "member_url"

try:
    detail = client.post_by_ref(post_ref)
    print(f"게시물 제목: {detail.title}")
    print(f"작성자: {detail.creator_nickname}")
    print(f"작성일: {detail.published_at}")
    print(f"본문 요약:\n{detail.text[:150]}...")

    if detail.images:
        print(f"\n포함된 이미지 ({len(detail.images)}개):")
        for img in detail.images[:3]:
            print(f" - {img}")

    if detail.attachments:
        print(f"\n첨부 파일 ({len(detail.attachments)}개):")
        for a in detail.attachments:
            print(f" - [{a.file_type}] {a.display_name} ({a.size_text})")

        output_dir = "./downloads"
        print(f"\n첨부파일 다운로드 진행 -> {output_dir}")
        saved_files = detail.download_attachments(output_dir)
        for f in saved_files:
            print(f"저장 완료: {f}")

except PostNotFound:
    print(f"게시물을 찾을 수 없습니다: {post_ref}")
except ValueError as e:
    print(f"입력 오류: {e}")
