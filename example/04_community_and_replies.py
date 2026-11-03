import sys
import os
from fanding import FandingClient

client = FandingClient()

post_no = post_number

print(f"=== 게시물 #{post_no} 댓글 및 대댓글 조회 ===")
try:
    page = client.replies(post_no, limit=10, sort="popular", with_children=True)
    for r in page.replies:
        print(f"[{r.nickname}] {r.content} (좋아요: {r.like_count})")
        for ch in r.children:
            print(f"  └ [{ch.nickname}] {ch.content}")
except Exception as e:
    print(f"댓글 조회 실패: {e}")

member_url = "stellive"
print(f"\n=== @{member_url} 커뮤니티 최신글 ===")
try:
    comm_page = client.community_feed(member_url, limit=5)
    for p in comm_page.posts:
        print(f"[{p.post_no}] {p.nickname}: {p.title or p.text[:30]}")
except Exception as e:
    print(f"커뮤니티 조회 실패: {e}")
