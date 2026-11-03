import sys
import os
from fanding import FandingClient, ChannelNotFound

client = FandingClient()
member_url = "member_name"

try:
    channel = client.channel(member_url)
    print(f"채널명: {channel.nickname} (@{channel.member_url})")
    print(f"채널 번호: {channel.creator_no}")
    print(f"소개: {channel.one_line}")
    print(f"프로필 이미지: {channel.profile_image_url}")
    print(f"메뉴 목록: {', '.join(channel.menu_names)}")

    tier_list = client.tiers(member_url)
    print(f"\n멤버십 플랜 ({len(tier_list)}개):")
    for tier in tier_list:
        price = f"{tier.price:,}원" if tier.price else "무료/미정"
        print(f" - [{tier.tier_no}] {tier.name} ({price})")

    tags = client.tags(member_url)
    if tags:
        print(f"\n등록된 태그: {', '.join(tags)}")

except ChannelNotFound:
    print(f"채널을 찾을 수 없습니다: {member_url}")
