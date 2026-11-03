import sys
import os
from fanding import FandingClient

client = FandingClient()
member_url = "member_name"

print(f"=== @{member_url} 최근 게시물 5개 ===")
posts = client.posts(member_url, limit=5, sort="recent")
for p in posts:
    when = p.published_at.strftime("%Y-%m-%d %H:%M") if p.published_at else "-"
    paid_mark = "[유료]" if p.is_paid else "[무료]"
    print(f"[{p.post_no}] {when} {paid_mark} {p.title} (조회: {p.view_count}, 좋아요: {p.like_count})")

print(f"\n=== @{member_url} 전체 게시물 순회 (최대 10개) ===")
for i, p in enumerate(client.iter_posts(member_url, limit=10), 1):
    print(f"{i:2d}. [{p.post_no}] {p.title}")
