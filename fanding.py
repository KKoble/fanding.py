from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union

__all__ = [
    "FandingClient",
    "Channel",
    "Post",
    "PostPage",
    "PostDetail",
    "Attachment",
    "Reply",
    "ReplyPage",
    "Creator",
    "Banner",
    "ChannelHome",
    "ChannelSection",
    "FeedPage",
    "Collection",
    "CommunityCategory",
    "CommunityPost",
    "CommunityPage",
    "MembershipTier",
    "MembershipTierList",
    "CreatorPage",
    "HotCreatorGroup",
    "ExplorerTab",
    "Emoticon",
    "ReplyPreset",
    "Country",
    "FandingError",
    "ChannelNotFound",
    "PostNotFound",
]

__version__ = "0.4.1"

DEFAULT_BASE_URL = "https://fanding.kr"

MAX_PAGE_SIZE = 24
MAX_REPLY_PAGE_SIZE = 200
MAX_FEED_PAGE_SIZE = 20
MAX_COMMUNITY_PAGE_SIZE = 20

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

KST = timezone(timedelta(hours=9))

_CODE_NOT_EXIST_POST = "FAIL_R/NOT_EXIST_POST"
_CODE_INVALID_PARAM = "ERROR_REQUEST_INVALID_PARAM"

SORT_ALIASES = {
    "recent": "recent",
    "latest": "recent",
    "new": "recent",
    "view": "view",
    "views": "view",
    "popular": "view",
    "like": "like",
    "likes": "like",
}

SEARCH_TYPE_ALIASES = {
    "title": "title",
    "content": "content",
    "body": "content",
}

REPLY_SORT_ALIASES = {
    "recent": "recent",
    "latest": "recent",
    "new": "recent",
    "popular": "popular",
    "like": "popular",
    "likes": "popular",
    "best": "popular",
}

COMMANDS = {
    "channel",
    "posts",
    "post",
    "replies",
    "feed",
    "series",
    "community",
    "tiers",
    "search",
    "explore",
}

MENU_LABELS = {
    "common.intro": "소개",
    "common.home": "홈",
    "common.post": "게시물",
    "common.series": "시리즈",
    "common.community": "커뮤니티",
    "common.membership": "멤버십",
    "common.store": "스토어",
    "common.course": "클래스",
    "common.event": "이벤트",
    "common.review": "리뷰",
    "channel.guide": "가이드",
}


class FandingError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, code: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.code = code


class ChannelNotFound(FandingError):
    pass


class PostNotFound(FandingError):
    pass


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_TAGS_RE = re.compile(r"(?i)</?(?:p|div|br|hr|h[1-6]|li|blockquote|tr)\b[^>]*>")
_INLINE_TAGS_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_IMG_RE = re.compile(r"(?i)<img[^>]+src=[\"']([^\"']+)[\"']")


def html_to_text(html: Optional[str]) -> str:
    if not html:
        return ""
    s = _SCRIPT_RE.sub(" ", html)
    s = _BLOCK_TAGS_RE.sub("\n", s)
    s = _INLINE_TAGS_RE.sub("", s)
    s = unescape(s)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in s.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract_images(html: Optional[str]) -> List[str]:
    if not html:
        return []
    return [unescape(m) for m in _IMG_RE.findall(html)]


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=KST)
    except (ValueError, TypeError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=KST)
        except ValueError:
            continue
    return None


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).upper() == "T"


def _dig(d: Any, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if isinstance(d, dict) and k in d:
            d = d[k]
        else:
            return default
    return d


def _duration_text(seconds: Optional[int]) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _size_text(size: Optional[int]) -> str:
    try:
        n = float(size or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


_POST_URL_RE = re.compile(r"@([^/\s?#]+)/posts?/(\d+)")
_POST_PATH_RE = re.compile(r"/posts?/(\d+)/?$")
_POST_PAIR_RE = re.compile(r"^@?([A-Za-z0-9_.-]+)/(\d+)/?$")


def parse_post_ref(ref: Any) -> Tuple[int, Optional[str]]:
    if isinstance(ref, int):
        return ref, None
    s = str(ref).strip()
    if s.isdigit():
        return int(s), None

    parsed = urllib.parse.urlsplit(s)
    clean_path = parsed.path.rstrip("/")

    m = _POST_URL_RE.search(s)
    if m:
        return int(m.group(2)), m.group(1)

    m = _POST_PATH_RE.search(clean_path)
    if m:
        return int(m.group(1)), None

    m = _POST_PAIR_RE.match(clean_path.lstrip("/"))
    if m:
        return int(m.group(2)), m.group(1)

    raise ValueError(f"게시물 번호를 읽을 수 없습니다: {ref!r}")


@dataclass
class Channel:
    member_url: str
    creator_no: int
    creator_member_no: Optional[int]
    nickname: str
    one_line: Optional[str]
    thumbnail_url: Optional[str]
    cover_image_url: Optional[str]
    mobile_cover_image_url: Optional[str]
    main_category: Optional[str]
    sub_category: Optional[str]
    is_launching: bool
    is_adult_only: bool
    menus: List[Dict[str, Any]] = field(default_factory=list)
    links: List[Dict[str, Any]] = field(default_factory=list)

    member_no: Optional[int] = None
    favicon_url: Optional[str] = None
    ribbon_banners: List[Dict[str, Any]] = field(default_factory=list)
    badge_image_url: Optional[str] = None
    home_layout: Optional[str] = None
    is_review: bool = False
    is_group_chat: bool = False
    is_following: bool = False
    is_fanding: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)
    _client: Optional["FandingClient"] = field(default=None, repr=False, compare=False)

    @property
    def url(self) -> str:
        return f"{DEFAULT_BASE_URL}/@{self.member_url}/"

    @property
    def posts_url(self) -> str:
        return f"{DEFAULT_BASE_URL}/@{self.member_url}/posts"

    @property
    def profile_image_url(self) -> Optional[str]:
        return self.thumbnail_url

    @property
    def banner_url(self) -> Optional[str]:
        return self.cover_image_url

    @property
    def mobile_banner_url(self) -> Optional[str]:
        return self.mobile_cover_image_url or self.cover_image_url

    @property
    def menu_names(self) -> List[str]:
        names = []
        for m in self.menus:
            code = m.get("sCode") or ""
            names.append(m.get("sCustomName") or MENU_LABELS.get(code) or code or m.get("sName", ""))
        return names

    @classmethod
    def from_api(cls, data: Dict[str, Any], client: Optional["FandingClient"] = None) -> "Channel":
        d = data.get("aCreatorData") or {}
        ch = cls(
            member_url=d.get("sMemberUrl") or "",
            creator_no=int(d.get("iCreatorNo") or 0),
            creator_member_no=d.get("iCreatorMemberNo"),
            nickname=d.get("sNickname") or "",
            one_line=d.get("sOneLine"),
            thumbnail_url=d.get("sThumbnailUrl"),
            cover_image_url=d.get("sCoverImagePath"),
            mobile_cover_image_url=d.get("sMobileCoverImagePath"),
            main_category=d.get("sCreatorMainCategoryCode"),
            sub_category=d.get("sCreatorSubCategoryCode"),
            is_launching=_flag(d.get("sIsLaunching")),
            is_adult_only=_flag(d.get("sIsAdultOnlyCreator")),
            menus=list(d.get("aMenuList") or []),
            links=list(d.get("aCreatorLinkList") or []),
            member_no=d.get("iCreatorMemberNo"),
            favicon_url=d.get("sFaviconImageUrl"),
            ribbon_banners=list(d.get("aRibbonBanner") or []),
            badge_image_url=data.get("sBadgeImageUrl"),
            home_layout=d.get("sHomeLayout"),
            is_review=_flag(d.get("sIsReview")),
            is_group_chat=bool(d.get("bIsGroupChat")),
            is_following=bool(data.get("bIsFollow")),
            is_fanding=bool(data.get("bIsFanding")),
            raw=data,
        )
        ch._client = client
        return ch


@dataclass
class Post:
    post_no: int
    title: str
    post_type: Optional[str]
    content_type: Optional[str]
    thumbnail_url: Optional[str]
    published_at: Optional[datetime]
    view_count: int
    like_count: int
    reply_count: int
    is_paid: bool
    is_locked: bool
    public_range: Optional[str]
    collection_no: Optional[int]
    creator_no: Optional[int]
    creator_member_url: Optional[str]
    creator_nickname: Optional[str]
    duration: int = 0
    blurred_thumbnail_url: Optional[str] = None

    heat: Optional[int] = None
    is_tax_free: bool = False
    vote_auth: Optional[str] = None
    is_bookmark: bool = False
    is_read: bool = False
    is_liked: bool = False
    url_meta_no: Optional[int] = None
    direct_url_code: Optional[str] = None
    status: Optional[str] = None
    deleted_at: Optional[datetime] = None
    reserved_public_at: Optional[datetime] = None
    reserved_private_at: Optional[datetime] = None
    writer_nickname: Optional[str] = None
    writer_thumbnail_url: Optional[str] = None
    creator_thumbnail_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    _client: Optional["FandingClient"] = field(default=None, repr=False, compare=False)
    _detail: Optional["PostDetail"] = field(default=None, repr=False, compare=False)

    @property
    def url(self) -> str:
        who = self.creator_member_url or ""
        return f"{DEFAULT_BASE_URL}/@{who}/post/{self.post_no}/"

    @property
    def is_video(self) -> bool:
        return self.content_type == "M" or self.duration > 0

    @classmethod
    def from_api(cls, d: Dict[str, Any], client: Optional["FandingClient"] = None) -> "Post":
        creator = d.get("aCreator") or {}
        auth = d.get("aAuthInfo") or {}
        post = cls(
            post_no=int(d.get("iPostNo") or 0),
            title=d.get("sTitle") or "",
            post_type=d.get("sType"),
            content_type=d.get("sContentType"),
            thumbnail_url=d.get("sPostThumbnailUrl"),
            published_at=_parse_datetime(d.get("sInsDatetime")),
            view_count=int(d.get("iViewCount") or 0),
            like_count=int(d.get("iLikeCount") or 0),
            reply_count=int(d.get("iReplyCount") or 0),
            is_paid=_flag(d.get("sIsPaidContent")),
            is_locked=bool(auth.get("bIsLock")),
            public_range=auth.get("sPublicRange"),
            collection_no=d.get("iCollectionNo"),
            creator_no=creator.get("iCreatorNo"),
            creator_member_url=creator.get("sCreatorUrl"),
            creator_nickname=creator.get("sNickname"),
            duration=int(d.get("iDuration") or 0),
            blurred_thumbnail_url=d.get("sBlurredPostThumbnailUrl"),
            heat=d.get("iHeat"),
            is_tax_free=bool(d.get("bIsTaxFree")),
            vote_auth=d.get("sVoteAuth"),
            is_bookmark=_flag(d.get("bIsBookmark")),
            is_read=_flag(d.get("bIsRead")),
            is_liked=_flag(d.get("sIsDoLike")),
            url_meta_no=d.get("iUrlMetaNo"),
            direct_url_code=d.get("sDirectUrl"),
            status=d.get("sStatus"),
            deleted_at=_parse_datetime(d.get("sDelDatetime")),
            reserved_public_at=_parse_datetime(d.get("sReservedPublicDatetime")),
            reserved_private_at=_parse_datetime(d.get("sReservedPrivateDatetime")),
            writer_nickname=(d.get("aWriterInfo") or {}).get("sNickname"),
            writer_thumbnail_url=(d.get("aWriterInfo") or {}).get("sThumbnailUrl"),
            creator_thumbnail_url=(d.get("aCreator") or {}).get("sThumbnailUrl"),
            raw=d,
        )
        post._client = client
        return post

    def fetch(self, client: Optional["FandingClient"] = None) -> "PostDetail":
        cli = client or self._client
        if not cli:
            raise ValueError("FandingClient 인스턴스가 필요합니다.")
        if self._detail is None:
            self._detail = cli.post(self.post_no)
        return self._detail


@dataclass
class Attachment:
    file_no: Optional[int]
    file_type: Optional[str]
    file_name: Optional[str]
    url: Optional[str]
    thumbnail_url: Optional[str]
    link_url: Optional[str]
    size: int = 0
    duration: int = 0
    sort_order: int = 0
    is_converting: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_image(self) -> bool:
        return (self.file_type or "").lower() == "image"

    @property
    def is_video(self) -> bool:
        return (self.file_type or "").lower() in ("video", "movie")

    @property
    def display_name(self) -> str:
        if self.file_name:
            return self.file_name
        path = urllib.parse.urlsplit(self.url or "").path
        raw_name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
        cleaned = re.sub(r'[\/*?:"<>|]', "_", raw_name).strip()
        return cleaned or "attachment"

    @property
    def duration_text(self) -> str:
        return _duration_text(self.duration)

    @property
    def size_text(self) -> str:
        return _size_text(self.size)

    def download(
        self,
        target_path: str,
        chunk_size: int = 65536,
        headers: Optional[Dict[str, str]] = None,
    ) -> str:
        if not self.url:
            raise ValueError("다운로드 가능한 URL이 없습니다.")
        req_headers = {"User-Agent": DEFAULT_USER_AGENT}
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(self.url, headers=req_headers)
        temp_path = f"{target_path}.tmp"
        with urllib.request.urlopen(req) as resp, open(temp_path, "wb") as f:
            while chunk := resp.read(chunk_size):
                f.write(chunk)
        if os.path.exists(target_path):
            os.remove(target_path)
        os.rename(temp_path, target_path)
        return target_path

    @classmethod
    def from_api(cls, f: Dict[str, Any]) -> "Attachment":
        url = f.get("sFullUploadUrl") or f.get("sLinkUrl") or None
        return cls(
            file_no=f.get("iFileNo"),
            file_type=f.get("sType"),
            file_name=f.get("sFileName") or None,
            url=url,
            thumbnail_url=f.get("sThumbnailUrl") or None,
            link_url=f.get("sLinkUrl") or None,
            size=int(f.get("iFileSize") or 0),
            duration=int(f.get("iDuration") or 0),
            sort_order=int(f.get("iSortOrder") or 0),
            is_converting=_flag(f.get("sIsConverting")),
            raw=f,
        )


@dataclass
class PostDetail:
    post_no: int
    title: str
    content_html: str
    published_at: Optional[datetime]
    creator_no: Optional[int]
    creator_member_url: Optional[str]
    creator_nickname: Optional[str]
    view_count: int
    like_count: int
    reply_count: int
    is_paid: bool
    is_locked: bool
    is_adult_only: bool
    status: Optional[str]
    tags: List[str] = field(default_factory=list)
    image_urls: List[str] = field(default_factory=list)
    file_urls: List[str] = field(default_factory=list)

    writer_nickname: Optional[str] = None
    writer_thumbnail_url: Optional[str] = None
    creator_thumbnail_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    blurred_thumbnail_url: Optional[str] = None
    preview_image_urls: List[str] = field(default_factory=list)
    attachments: List["Attachment"] = field(default_factory=list)
    file_count: int = 0
    file_counts: Dict[str, int] = field(default_factory=dict)
    content_type: Optional[str] = None
    duration: int = 0
    is_app_only: bool = False
    is_blur: bool = False
    is_fixed_top: bool = False
    is_fixed_home: bool = False
    is_liked: bool = False
    paid_summary: Dict[str, Any] = field(default_factory=dict)
    collection: Optional[Dict[str, Any]] = None
    reply_permission: Optional[str] = None
    replies_blocked: bool = False
    reply_sort: Optional[str] = None
    direct_url_code: Optional[str] = None
    representative_reply: Optional["Reply"] = None

    is_bookmark: bool = False
    is_read: bool = False
    url_meta_no: Optional[int] = None
    deleted_at: Optional[datetime] = None
    reserved_public_at: Optional[datetime] = None
    reserved_private_at: Optional[datetime] = None
    paid_content: Optional[str] = None
    advisory_notice_text: Optional[str] = None
    is_today_mission: bool = False
    is_investment_advisory_notice: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)
    _client: Optional["FandingClient"] = field(default=None, repr=False, compare=False)

    @property
    def url(self) -> str:
        who = self.creator_member_url or ""
        return f"{DEFAULT_BASE_URL}/@{who}/post/{self.post_no}/"

    @property
    def text(self) -> str:
        return html_to_text(self.content_html)

    @property
    def is_video(self) -> bool:
        return self.content_type == "M" or self.duration > 0

    @property
    def duration_text(self) -> str:
        return _duration_text(self.duration)

    @property
    def images(self) -> List[str]:
        if self.image_urls:
            return list(self.image_urls)
        found = extract_images(self.content_html)
        if found:
            return found
        return [a.url for a in self.attachments if a.is_image and a.url]

    @property
    def files(self) -> List["Attachment"]:
        return [a for a in self.attachments if not a.is_image]

    @property
    def videos(self) -> List["Attachment"]:
        return [a for a in self.attachments if a.is_video]

    def comments(
        self,
        client: Optional["FandingClient"] = None,
        limit: int = 50,
        sort: str = "recent",
        with_children: bool = False,
    ) -> "ReplyPage":
        cli = client or self._client
        if not cli:
            raise ValueError("FandingClient 인스턴스가 필요합니다.")
        return cli.replies(self.post_no, limit=limit, sort=sort, with_children=with_children)

    def download_attachments(self, target_dir: str) -> List[str]:
        os.makedirs(target_dir, exist_ok=True)
        downloaded = []
        headers = self._client._headers if self._client else None
        for a in self.attachments:
            if not a.url:
                continue
            path = os.path.join(target_dir, a.display_name)
            a.download(path, headers=headers)
            downloaded.append(path)
        return downloaded

    @classmethod
    def from_api(
        cls,
        d: Dict[str, Any],
        fallback_member_url: Optional[str] = None,
        client: Optional["FandingClient"] = None,
    ) -> "PostDetail":
        creator = d.get("aCreatorInfo") or d.get("aCreator") or {}
        writer = d.get("aWriterInfo") or {}
        auth = d.get("aAuthInfo") or {}
        reply_auth = d.get("aReplyAuth") or {}
        files = d.get("aFileList") or []
        attachments = [Attachment.from_api(f) for f in files]
        file_urls = [a.url for a in attachments if a.url]
        reply_raw = d.get("aReply")
        detail = cls(
            post_no=int(d.get("iPostNo") or 0),
            title=d.get("sTitle") or "",
            content_html=d.get("sContent") or "",
            published_at=_parse_datetime(d.get("sInsDatetime")),
            creator_no=creator.get("iCreatorNo"),
            creator_member_url=creator.get("sCreatorUrl") or fallback_member_url,
            creator_nickname=creator.get("sNickname"),
            view_count=int(d.get("iViewCount") or 0),
            like_count=int(d.get("iLikeCount") or 0),
            reply_count=int(d.get("iReplyCount") or 0),
            is_paid=_flag(d.get("sIsPaidContent")) or _flag(d.get("sIsPaid")),
            is_locked=_flag(auth.get("sIsLock")) or bool(auth.get("bIsLock")),
            is_adult_only=_flag(auth.get("sIsAdultOnly")),
            status=d.get("sStatus"),
            tags=list(d.get("aTagList") or []),
            image_urls=list(d.get("aImageList") or []),
            file_urls=file_urls,
            writer_nickname=writer.get("sNickname"),
            writer_thumbnail_url=writer.get("sThumbnailUrl"),
            creator_thumbnail_url=creator.get("sThumbnailUrl"),
            thumbnail_url=d.get("sPostThumbnailUrl"),
            blurred_thumbnail_url=d.get("sBlurredPostThumbnailUrl"),
            preview_image_urls=list(d.get("aPreviewImageList") or []),
            attachments=attachments,
            file_count=int(d.get("iFileCount") or 0),
            file_counts=dict(d.get("aFileCounts") or {}),
            content_type=d.get("sContentType"),
            duration=int(d.get("iDuration") or 0),
            is_app_only=_flag(d.get("sIsAppOnly")),
            is_blur=_flag(d.get("sIsBlur")),
            is_fixed_top=_flag(d.get("sIsFixTop")),
            is_fixed_home=_flag(d.get("sIsFixHome")),
            is_liked=_flag(d.get("sIsDoLike")),
            paid_summary=dict(d.get("aPaidContentSummary") or {}),
            collection=d.get("aCollectionData"),
            reply_permission=reply_auth.get("sReadPermission"),
            replies_blocked=_flag(reply_auth.get("sIsBlock")),
            reply_sort=d.get("sReplyDefaultSort"),
            direct_url_code=d.get("sDirectUrl"),
            representative_reply=Reply.from_api(reply_raw) if isinstance(reply_raw, dict) else None,
            is_bookmark=_flag(d.get("bIsBookmark")),
            is_read=_flag(d.get("bIsRead")),
            url_meta_no=d.get("iUrlMetaNo"),
            deleted_at=_parse_datetime(d.get("sDelDatetime")),
            reserved_public_at=_parse_datetime(d.get("sReservedPublicDatetime")),
            reserved_private_at=_parse_datetime(d.get("sReservedPrivateDatetime")),
            paid_content=d.get("sPaidContent"),
            advisory_notice_text=d.get("sInvestmentAdvisoryNoticeText"),
            is_today_mission=_flag(d.get("bIsTodayMission")),
            is_investment_advisory_notice=_flag(d.get("bIsInvestmentAdvisoryNoticeInserted")),
            raw=d,
        )
        detail._client = client
        return detail


@dataclass
class PostPage:
    posts: List[Post]
    total_count: int
    tags: List[Dict[str, Any]] = field(default_factory=list)
    counts_by_type: Dict[str, int] = field(default_factory=dict)
    months: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[Post]:
        return iter(self.posts)

    def __len__(self) -> int:
        return len(self.posts)

    def __getitem__(self, index: int) -> Post:
        return self.posts[index]

    @classmethod
    def from_api(cls, data: Dict[str, Any], client: Optional["FandingClient"] = None) -> "PostPage":
        return cls(
            posts=[Post.from_api(p, client=client) for p in (data.get("aPostList") or [])],
            total_count=int(data.get("iTotalCount") or 0),
            tags=list(data.get("aTagCount") or []),
            counts_by_type=dict(data.get("aPostCountByType") or {}),
            months=list(data.get("aUploadDate") or []),
            raw=data,
        )


@dataclass
class Reply:
    reply_no: int
    post_no: Optional[int]
    nickname: str
    content: str
    published_at: Optional[datetime]
    member_no: Optional[int] = None
    member_url: Optional[str] = None
    fan_page_url: Optional[str] = None
    profile_image_url: Optional[str] = None
    like_count: int = 0
    child_reply_count: int = 0
    has_creator_child_reply: bool = False
    is_creator: bool = False
    is_fander: bool = False
    is_creator_liked: bool = False
    is_mine: bool = False
    is_liked: bool = False
    is_fixed: bool = False
    badge_image_url: Optional[str] = None
    profile_badges: List[Dict[str, Any]] = field(default_factory=list)
    emoticon_image_url: Optional[str] = None
    parent_reply_no: Optional[int] = None
    children: List["Reply"] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_children(self) -> bool:
        return bool(self.children) or self.child_reply_count > 0

    @property
    def is_reply(self) -> bool:
        return self.parent_reply_no is not None

    @classmethod
    def from_api(cls, d: Dict[str, Any]) -> "Reply":
        return cls(
            reply_no=int(d.get("iReplyNo") or 0),
            post_no=d.get("iPostNo"),
            nickname=d.get("sNickname") or "",
            content=d.get("sContent") or "",
            published_at=_parse_datetime(d.get("sInsDatetime")),
            member_no=d.get("iMemberNo"),
            member_url=d.get("sMemberUrl"),
            fan_page_url=d.get("sFanPageUrl"),
            profile_image_url=d.get("sThumbnailUrl"),
            like_count=int(d.get("iLikeCount") or 0),
            child_reply_count=int(d.get("iChildReplyCount") or 0),
            has_creator_child_reply=_flag(d.get("sHasCreatorChildReply")),
            is_creator=_flag(d.get("sIsCreator")),
            is_fander=bool(d.get("bIsFander")),
            is_creator_liked=_flag(d.get("sIsCreatorLike")),
            is_mine=_flag(d.get("sIsMyReply")),
            is_liked=_flag(d.get("sIsDoLike")),
            is_fixed=_flag(d.get("sIsFixTop")),
            badge_image_url=d.get("sBadgeImageUrl"),
            profile_badges=list(d.get("aProfileBadges") or []),
            emoticon_image_url=d.get("sEmoticonImageUrl"),
            parent_reply_no=d.get("iParentNo") or d.get("iParentReplyNo"),
            raw=d,
        )


@dataclass
class ReplyPage:
    replies: List[Reply] = field(default_factory=list)
    total_count: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[Reply]:
        return iter(self.replies)

    def __len__(self) -> int:
        return len(self.replies)

    def __getitem__(self, index: int) -> Reply:
        return self.replies[index]

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "ReplyPage":
        return cls(
            replies=[Reply.from_api(r) for r in (data.get("aParentReplyList") or [])],
            total_count=int(data.get("iTotalCount") or 0),
            raw=data,
        )


@dataclass
class Creator:
    creator_no: int = 0
    nickname: str = ""
    member_url: Optional[str] = None
    one_line: Optional[str] = None
    thumbnail_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    main_category: Optional[str] = None
    sub_category: Optional[str] = None
    is_adult_only: bool = False
    is_following: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"{DEFAULT_BASE_URL}/@{self.member_url}/" if self.member_url else ""

    @classmethod
    def from_api(cls, d: Optional[Dict[str, Any]]) -> "Creator":
        d = d or {}
        return cls(
            creator_no=int(d.get("iCreatorNo") or 0),
            nickname=d.get("sNickname") or d.get("sCreatorName") or "",
            member_url=d.get("sCreatorUrl") or d.get("sMemberUrl"),
            one_line=d.get("sOneLine") or d.get("sCreatorOneLine") or d.get("sOneLineDescription"),
            thumbnail_url=d.get("sThumbnailUrl") or d.get("sCreatorThumbnailUrl"),
            cover_image_url=d.get("sCreatorCoverImageUrl"),
            main_category=d.get("sCreatorMainCategoryCode") or d.get("sMainCategoryCode"),
            sub_category=d.get("sSubCategoryCode") or d.get("sCreatorSubCategoryCode"),
            is_adult_only=_flag(d.get("sIsAdultOnly")) or bool(d.get("bIsAdultOnly")),
            is_following=_flag(d.get("sIsFollowing")),
            raw=d,
        )


@dataclass
class Banner:
    banner_no: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    mobile_image_url: Optional[str] = None
    link_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: Dict[str, Any]) -> "Banner":
        d = d or {}
        return cls(
            banner_no=d.get("iBannerNo") or d.get("iNo"),
            title=d.get("sTitle"),
            description=d.get("sDescription"),
            image_url=d.get("sImageUrl"),
            mobile_image_url=d.get("sMobileImageUrl"),
            link_url=d.get("sLinkUrl") or d.get("sPath"),
            raw=d,
        )


@dataclass
class ChannelHome:
    nickname: Optional[str] = None
    one_line: Optional[str] = None
    thumbnail_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    mobile_cover_image_url: Optional[str] = None
    main_category: Optional[str] = None
    sub_category: Optional[str] = None
    follower_count: Optional[int] = None
    fander_count: Optional[int] = None
    total_fander_count: Optional[int] = None
    post_count: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: Dict[str, Any]) -> "ChannelHome":
        d = d or {}
        return cls(
            nickname=d.get("sCreatorNickname"),
            one_line=d.get("sOneLine"),
            thumbnail_url=d.get("sThumbnailUrl"),
            cover_image_url=d.get("sCoverImageUrl"),
            mobile_cover_image_url=d.get("sMobileCoverImageUrl"),
            main_category=d.get("sMainCategoryCode"),
            sub_category=d.get("sSubCategoryCode"),
            follower_count=d.get("iFollowerCount"),
            fander_count=d.get("iFanderCount"),
            total_fander_count=d.get("iTotalFanderCount"),
            post_count=d.get("iPostCount"),
            raw=d,
        )


@dataclass
class ChannelSection:
    section_no: Optional[int]
    title: Optional[str]
    section_type: Optional[str]
    total_count: int = 0
    description: Optional[str] = None
    tag: Optional[str] = None
    posts: List["Post"] = field(default_factory=list)
    collections: List["Collection"] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    reviews: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: Dict[str, Any], client: Optional["FandingClient"] = None) -> "ChannelSection":
        d = d or {}
        item = d.get("aSectionItem") or {}
        return cls(
            section_no=d.get("iSectionNo"),
            title=d.get("sTitle"),
            section_type=d.get("sType"),
            total_count=int(d.get("iTotalCount") or 0),
            description=d.get("sDescription"),
            tag=d.get("sTag"),
            posts=[Post.from_api(p, client=client) for p in (item.get("aPostList") or [])],
            collections=[Collection.from_api(c) for c in (item.get("aCollectionList") or [])],
            events=list(item.get("aEventList") or []),
            reviews=list(item.get("aReviewList") or []),
            raw=d,
        )


@dataclass
class FeedPage:
    posts: List["PostDetail"] = field(default_factory=list)
    total_count: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[PostDetail]:
        return iter(self.posts)

    def __len__(self) -> int:
        return len(self.posts)

    def __getitem__(self, index: int) -> PostDetail:
        return self.posts[index]

    @classmethod
    def from_api(cls, data: Dict[str, Any], client: Optional["FandingClient"] = None) -> "FeedPage":
        data = data or {}
        return cls(
            posts=[PostDetail.from_api(p, client=client) for p in (data.get("aFeedList") or [])],
            total_count=int(data.get("iTotalCount") or 0),
            raw=data,
        )


@dataclass
class Collection:
    collection_no: int = 0
    title: str = ""
    description: Optional[str] = None
    thumbnail_url: Optional[str] = None
    post_count: int = 0
    like_count: int = 0
    first_post_no: Optional[int] = None
    creator_no: Optional[int] = None
    creator_nickname: Optional[str] = None
    creator_thumbnail_url: Optional[str] = None
    is_liked: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: Dict[str, Any]) -> "Collection":
        d = d or {}
        return cls(
            collection_no=int(d.get("iNo") or d.get("iCollectionNo") or 0),
            title=d.get("sTitle") or "",
            description=d.get("sDescription"),
            thumbnail_url=d.get("sThumbnailUrl"),
            post_count=int(d.get("iTotalPostCount") or d.get("iPostCount") or 0),
            like_count=int(d.get("iLikeCount") or 0),
            first_post_no=d.get("iFirstPostNo"),
            creator_no=d.get("iCreatorNo"),
            creator_nickname=d.get("sCreatorNickname"),
            creator_thumbnail_url=d.get("sCreatorThumbnailUrl"),
            is_liked=bool(d.get("bIsLike")),
            raw=d,
        )


@dataclass
class CommunityCategory:
    category_no: Optional[int]
    name: str = ""
    description: Optional[str] = None
    write_permission: Optional[str] = None
    read_permission: Optional[str] = None
    is_write_allowed: bool = False
    layout_type: Optional[str] = None
    tiers: List[Dict[str, Any]] = field(default_factory=list)
    challenge: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: Dict[str, Any]) -> "CommunityCategory":
        d = d or {}
        return cls(
            category_no=d.get("iCategoryNo"),
            name=d.get("sName") or "",
            description=d.get("sDescription"),
            write_permission=d.get("sWritePermission"),
            read_permission=d.get("sReadPermission"),
            is_write_allowed=_flag(d.get("sIsWriteAllowed")),
            layout_type=d.get("sDefaultLayoutType"),
            tiers=list(d.get("aTierList") or []),
            challenge=d.get("aChallenge") or d.get("challenge"),
            raw=d,
        )


@dataclass
class CommunityPost:
    post_no: int = 0
    title: Optional[str] = None
    content_html: str = ""
    nickname: Optional[str] = None
    member_no: Optional[int] = None
    member_thumbnail_url: Optional[str] = None
    badge_image_url: Optional[str] = None
    profile_badges: List[Dict[str, Any]] = field(default_factory=list)
    category_no: Optional[int] = None
    category_name: Optional[str] = None
    published_at: Optional[datetime] = None
    view_count: int = 0
    like_count: int = 0
    reply_count: int = 0
    is_locked: bool = False
    is_fixed_top: bool = False
    public_range: Optional[str] = None
    fan_page_url: Optional[str] = None
    is_writer: bool = False
    is_writer_creator: bool = False
    is_writer_fander: bool = False
    is_creator_liked: bool = False
    is_liked: bool = False
    is_recommended: bool = False
    popular_rank: Optional[int] = None
    images: List[str] = field(default_factory=list)
    video: Optional[Dict[str, Any]] = None
    preview_link: Optional[Dict[str, Any]] = None
    creator_reply: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return html_to_text(self.content_html)

    @classmethod
    def from_api(cls, d: Dict[str, Any]) -> "CommunityPost":
        d = d or {}
        cat = d.get("aCategory") or {}
        return cls(
            post_no=int(d.get("iCommunityNo") or 0),
            title=d.get("sTitle"),
            content_html=d.get("sContent") or "",
            nickname=d.get("sNickname"),
            member_no=d.get("iMemberNo"),
            member_thumbnail_url=d.get("sMemberThumbnailUrl"),
            badge_image_url=d.get("sBadgeImageUrl"),
            profile_badges=list(d.get("aProfileBadges") or []),
            category_no=cat.get("iCategoryNo"),
            category_name=cat.get("sCategoryName") or d.get("sCategoryName"),
            published_at=_parse_datetime(d.get("sInsDatetime")),
            view_count=int(d.get("iViewCount") or 0),
            like_count=int(d.get("iLikeCount") or 0),
            reply_count=int(d.get("iReplyCount") or 0),
            is_locked=_flag(d.get("sIsLock")),
            is_fixed_top=_flag(d.get("sIsFixTop")),
            public_range=d.get("sPublicRange"),
            fan_page_url=d.get("sFanPageUrl"),
            is_writer=_flag(d.get("sIsWriter")),
            is_writer_creator=_flag(d.get("sIsWriterCreator")),
            is_writer_fander=_flag(d.get("sIsWriterFander")),
            is_creator_liked=_flag(d.get("bIsCreatorLike")),
            is_liked=_flag(d.get("sIsDoLike")),
            is_recommended=_flag(d.get("bIsRecommended")),
            popular_rank=d.get("iPopularRank"),
            images=list(d.get("aImageList") or []),
            video=d.get("aVideo") or None,
            preview_link=d.get("aPreviewLinkContent") or None,
            creator_reply=d.get("aCreatorReply") or None,
            raw=d,
        )


@dataclass
class CommunityPage:
    posts: List[CommunityPost] = field(default_factory=list)
    total_count: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[CommunityPost]:
        return iter(self.posts)

    def __len__(self) -> int:
        return len(self.posts)

    def __getitem__(self, index: int) -> CommunityPost:
        return self.posts[index]

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "CommunityPage":
        data = data or {}
        return cls(
            posts=[CommunityPost.from_api(p) for p in (data.get("aCommunityList") or [])],
            total_count=int(data.get("iTotalCount") or 0),
            raw=data,
        )


@dataclass
class MembershipTier:
    tier_no: int = 0
    name: str = ""
    description: Optional[str] = None
    detail_description: Optional[str] = None
    price: Optional[int] = None
    regular_price: Optional[int] = None
    end_criteria: Optional[str] = None
    period: Optional[int] = None
    end_date: Optional[str] = None
    join_start_date: Optional[str] = None
    join_end_date: Optional[str] = None
    join_period_start_date: Optional[str] = None
    join_period_remain_day: Optional[int] = None
    public_status: Optional[str] = None
    seat_status: Optional[str] = None
    is_renewable: bool = False
    is_monthly_pass_allowed: bool = False
    is_tax_free: bool = False
    is_approval_required: bool = False
    is_message_available: bool = False
    is_end: bool = False
    is_heat_block: bool = False
    is_first_payment_discount: bool = False
    image_url: Optional[str] = None
    badge_image_url: Optional[str] = None
    limit_count: Optional[int] = None
    fanding_count: int = 0
    post_count: int = 0
    product_count: int = 0
    course_count: int = 0
    event_count: int = 0
    review_count: int = 0
    survey_count: int = 0
    discounts: List[Dict[str, Any]] = field(default_factory=list)
    rewards: List[Dict[str, Any]] = field(default_factory=list)
    creator: Optional[Creator] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: Dict[str, Any], creator: Optional[Creator] = None) -> "MembershipTier":
        d = d or {}
        return cls(
            tier_no=int(d.get("iTierNo") or 0),
            name=d.get("sName") or "",
            description=d.get("sDescription"),
            detail_description=d.get("sDetailDescription"),
            price=d.get("iPrice"),
            regular_price=d.get("iRegularPrice"),
            end_criteria=d.get("sEndCriteria"),
            period=d.get("iPeriod"),
            end_date=d.get("sEndDate"),
            join_start_date=d.get("sJoinStartDate"),
            join_end_date=d.get("sJoinEndDate"),
            join_period_start_date=d.get("sJoinPeriodStartDate"),
            join_period_remain_day=d.get("iJoinPeriodRemainDay"),
            public_status=d.get("sPublicStatus"),
            seat_status=d.get("sSeatStatus"),
            is_renewable=_flag(d.get("sIsRenewable")),
            is_monthly_pass_allowed=_flag(d.get("sIsMonthlyPassAllowed")),
            is_tax_free=_flag(d.get("sIsTaxFree")),
            is_approval_required=_flag(d.get("sIsApprovalRequired")),
            is_message_available=_flag(d.get("sIsMessageAvailable")),
            is_end=_flag(d.get("sIsEnd")),
            is_heat_block=_flag(d.get("sIsTierHeatBlock")),
            is_first_payment_discount=_flag(d.get("sIsFirstPaymentDiscount")),
            image_url=d.get("sImageUrl"),
            badge_image_url=d.get("sBadgeImageUrl"),
            limit_count=d.get("iLimitCount"),
            fanding_count=int(d.get("iFandingCount") or 0),
            post_count=int(d.get("iPostCount") or 0),
            product_count=int(d.get("iProductCount") or 0),
            course_count=int(d.get("iCourseCount") or 0),
            event_count=int(d.get("iEventCount") or 0),
            review_count=int(d.get("iReviewCount") or 0),
            survey_count=int(d.get("iSurveyCount") or 0),
            discounts=list(d.get("aAppliedDiscountList") or []),
            rewards=list(d.get("aRewardList") or []),
            creator=creator,
            raw=d,
        )


@dataclass
class MembershipTierList:
    creator: Optional[Creator] = None
    tiers: List[MembershipTier] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[MembershipTier]:
        return iter(self.tiers)

    def __len__(self) -> int:
        return len(self.tiers)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "MembershipTierList":
        data = data or {}
        creator = Creator.from_api(data.get("aCreator")) if data.get("aCreator") else None
        return cls(
            creator=creator,
            tiers=[MembershipTier.from_api(t, creator=creator) for t in (data.get("aTierList") or [])],
            raw=data,
        )


@dataclass
class CreatorPage:
    creators: List[Creator] = field(default_factory=list)
    total_count: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[Creator]:
        return iter(self.creators)

    def __len__(self) -> int:
        return len(self.creators)

    def __getitem__(self, index: int) -> Creator:
        return self.creators[index]

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "CreatorPage":
        data = data or {}
        return cls(
            creators=[Creator.from_api(c) for c in (data.get("aCreatorList") or [])],
            total_count=int(data.get("iTotalCount") or 0),
            raw=data,
        )


@dataclass
class HotCreatorGroup:
    category_no: Optional[int]
    creators: List[Creator] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: Dict[str, Any]) -> "HotCreatorGroup":
        d = d or {}
        return cls(
            category_no=d.get("iCategoryNo"),
            creators=[Creator.from_api(c) for c in (d.get("aCreatorList") or [])],
            raw=d,
        )


@dataclass
class ExplorerTab:
    tab_no: Optional[int]
    name: Optional[str] = None
    name_by_lang: Dict[str, Any] = field(default_factory=list)
    image_url: Optional[str] = None
    main_category_no: Optional[int] = None
    sub_categories: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: Dict[str, Any]) -> "ExplorerTab":
        d = d or {}
        langs = d.get("aTabNameLang") or {}
        return cls(
            tab_no=d.get("iTabNo"),
            name=langs.get("ko") or d.get("sMainCategoryCode"),
            name_by_lang=dict(langs),
            image_url=d.get("sMainCategoryImageUrl"),
            main_category_no=d.get("iMainCategoryNo"),
            sub_categories=list(d.get("aSubCategoryList") or []),
            raw=d,
        )


@dataclass
class Emoticon:
    emoticon_no: Optional[int]
    image_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: Dict[str, Any]) -> "Emoticon":
        d = d or {}
        return cls(emoticon_no=d.get("iEmoticonNo"), image_url=d.get("sImageUrl"), raw=d)


@dataclass
class ReplyPreset:
    preset_no: Optional[int]
    code: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: Dict[str, Any]) -> "ReplyPreset":
        d = d or {}
        return cls(preset_no=d.get("iReplyPresetNo"), code=d.get("sCode"), raw=d)


@dataclass
class Country:
    lang_code: Optional[str]
    dial_code: Optional[str] = None
    verification_required: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, d: Dict[str, Any]) -> "Country":
        d = d or {}
        return cls(
            lang_code=d.get("sLangCode"),
            dial_code=d.get("sCountryNumber"),
            verification_required=_flag(d.get("sIsVerificationRequired")),
            raw=d,
        )


class FandingClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 15.0,
        retries: int = 2,
        user_agent: str = DEFAULT_USER_AGENT,
        headers: Optional[Dict[str, str]] = None,
        auth_token: Optional[str] = None,
        cookies: Optional[Union[Dict[str, str], str]] = None,
        delay: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.delay = max(0.0, float(delay))
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": f"{self.base_url}/",
        }
        if cookies:
            if isinstance(cookies, dict):
                self._headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            else:
                self._headers["Cookie"] = str(cookies)
        if auth_token:
            self._headers["Authorization"] = f"Bearer {auth_token}"
        if headers:
            self._headers.update(headers)

    def _request(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"

        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, headers=self._headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8", "replace")
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    last_exc = e
                    retry_after = e.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else (0.5 * (attempt + 1))
                    except (ValueError, TypeError):
                        wait = 0.5 * (attempt + 1)
                    time.sleep(wait)
                    continue
                raise self._error_from_response(e.code, body, url) from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_exc = e
                if attempt < self.retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise FandingError(f"요청 실패: {url} ({e})") from e
        else:
            raise FandingError(f"요청 실패: {url} ({last_exc})")

        if self.delay:
            time.sleep(self.delay)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise FandingError(f"JSON 이 아닌 응답: {url}", code="INVALID_RESPONSE") from None

        if isinstance(payload, dict) and payload.get("bIsResult") is False:
            raise FandingError(
                payload.get("sMessage") or payload.get("sCode") or "API 오류",
                code=payload.get("sCode"),
            )
        return payload.get("aData", payload)

    @staticmethod
    def _error_from_response(status: int, body: str, url: str) -> FandingError:
        message, code = "", None
        try:
            payload = json.loads(body)
            message = (
                payload.get("sMessage")
                or payload.get("sErrMessage")
                or payload.get("message")
                or ""
            )
            code = payload.get("sCode")
        except Exception:
            message = re.sub(r"<[^>]+>", " ", body)[:200].strip()
        detail = message or code or f"HTTP {status}"
        if status == 404 or code == _CODE_NOT_EXIST_POST:
            return PostNotFound(f"{detail}: {url}", status=status, code=code)
        return FandingError(f"{detail}: {url}", status=status, code=code)

    def channel(self, member_url: str) -> Channel:
        member_url = self._normalize_member_url(member_url)
        try:
            data = self._request("/rest/channel", {"sMemberUrl": member_url})
        except PostNotFound:
            raise ChannelNotFound(f"채널을 찾을 수 없습니다: {member_url}") from None
        except FandingError as e:
            if e.code == _CODE_INVALID_PARAM or e.status in (400, 404):
                raise ChannelNotFound(f"채널을 찾을 수 없습니다: {member_url}") from None
            raise
        ch = Channel.from_api(data or {}, client=self)
        if not ch.creator_no:
            raise ChannelNotFound(f"채널을 찾을 수 없습니다: {member_url}")
        return ch

    def post_list(
        self,
        member_url: str,
        limit: int = MAX_PAGE_SIZE,
        sort: str = "recent",
        search: Optional[str] = None,
        search_type: str = "title",
        after_post_no: Optional[int] = None,
    ) -> PostPage:
        member_url = self._normalize_member_url(member_url)
        sort_value = SORT_ALIASES.get(str(sort).lower(), str(sort))
        search_type_value = None
        if search:
            search_type_value = SEARCH_TYPE_ALIASES.get(str(search_type).lower(), str(search_type))

        try:
            data = self._request(
                "/rest/post_list",
                {
                    "sMemberUrl": member_url,
                    "iLimit": max(1, min(int(limit), MAX_PAGE_SIZE)),
                    "sSortOrder": sort_value,
                    "sVisibleOnlyOption": "F",
                    "iLastPostNo": after_post_no,
                    "sSearchInput": search,
                    "sSearchType": search_type_value,
                },
            )
        except FandingError as e:
            if e.code == _CODE_INVALID_PARAM:
                raise ChannelNotFound(f"채널을 찾을 수 없습니다: {member_url}") from None
            raise
        return PostPage.from_api(data or {}, client=self)

    def iter_posts(
        self,
        member_url: str,
        limit: Optional[int] = None,
        sort: str = "recent",
        search: Optional[str] = None,
        search_type: str = "title",
        page_size: int = MAX_PAGE_SIZE,
        max_pages: int = 500,
    ) -> Iterator[Post]:
        page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        seen: set[int] = set()
        yielded = 0
        cursor: Optional[int] = None
        for _ in range(max_pages):
            page = self.post_list(
                member_url,
                limit=page_size,
                sort=sort,
                search=search,
                search_type=search_type,
                after_post_no=cursor,
            )
            if not page.posts:
                return
            new = 0
            for post in page.posts:
                if post.post_no in seen:
                    continue
                seen.add(post.post_no)
                new += 1
                yield post
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if new == 0 or len(page.posts) < page_size:
                return
            cursor = page.posts[-1].post_no

    def posts(
        self,
        member_url: str,
        limit: int = 24,
        sort: str = "recent",
        search: Optional[str] = None,
        search_type: str = "title",
    ) -> List[Post]:
        return list(
            self.iter_posts(
                member_url, limit=limit, sort=sort, search=search, search_type=search_type
            )
        )

    def post(self, post_no: int) -> PostDetail:
        try:
            data = self._request("/rest/post", {"iPostNo": int(post_no)})
        except FandingError as e:
            if e.status in (400, 404) or e.code in (_CODE_NOT_EXIST_POST, _CODE_INVALID_PARAM):
                raise PostNotFound(f"게시물을 찾을 수 없습니다: {post_no}") from None
            raise
        detail = (data or {}).get("oPostData")
        if not detail:
            raise PostNotFound(f"게시물을 찾을 수 없습니다: {post_no}") from None
        return PostDetail.from_api(detail, client=self)

    def post_by_ref(self, ref: Any) -> PostDetail:
        post_no, member_url = parse_post_ref(ref)
        detail = self.post(post_no)
        if not detail.creator_member_url and member_url:
            detail.creator_member_url = member_url
        return detail

    def reply_page(
        self,
        post_no: int,
        limit: int = 50,
        sort: str = "recent",
        after_reply_no: Optional[int] = None,
        my_comments_only: bool = False,
    ) -> ReplyPage:
        try:
            data = self._request(
                "/rest/post_reply_list",
                {
                    "iPostNo": int(post_no),
                    "iLimit": max(1, min(int(limit), MAX_REPLY_PAGE_SIZE)),
                    "sSortOrder": REPLY_SORT_ALIASES.get(str(sort).lower(), str(sort)),
                    "sMyCommentOnly": "T" if my_comments_only else "F",
                    "iLastReplyNo": after_reply_no,
                },
            )
        except FandingError as e:
            if e.status in (400, 404) or e.code in (_CODE_NOT_EXIST_POST, _CODE_INVALID_PARAM):
                raise PostNotFound(f"게시물을 찾을 수 없습니다: {post_no}") from None
            raise
        return ReplyPage.from_api(data or {})

    def reply_list(self, post_no: int, limit: int = 50, sort: str = "recent", **kw) -> List[Reply]:
        return self.reply_page(post_no, limit=limit, sort=sort, **kw).replies

    def child_replies(
        self,
        parent_reply_no: int,
        limit: int = 20,
        after_reply_no: Optional[int] = None,
    ) -> List[Reply]:
        data = self._request(
            "/rest/post_reply_list/child",
            {
                "iParentNo": int(parent_reply_no),
                "iLimit": max(1, min(int(limit), MAX_REPLY_PAGE_SIZE)),
                "iLastReplyNo": after_reply_no,
            },
        )
        rows = (data or {}).get("aReplyList") or []
        return [Reply.from_api(r) for r in rows]

    def replies(
        self,
        post_no: int,
        limit: int = 50,
        sort: str = "recent",
        after_reply_no: Optional[int] = None,
        my_comments_only: bool = False,
        with_children: bool = False,
        max_pages: int = 100,
    ) -> ReplyPage:
        want = max(1, int(limit))
        page_size = min(want, MAX_REPLY_PAGE_SIZE)
        collected: List[Reply] = []
        seen: set[int] = set()
        total = 0
        cursor = after_reply_no
        for _ in range(max(1, max_pages)):
            page = self.reply_page(
                post_no,
                limit=page_size,
                sort=sort,
                after_reply_no=cursor,
                my_comments_only=my_comments_only,
            )
            total = page.total_count or total
            new = 0
            for r in page.replies:
                if r.reply_no in seen:
                    continue
                seen.add(r.reply_no)
                collected.append(r)
                new += 1
                if len(collected) >= want:
                    break
            if new == 0 or len(collected) >= want or len(page.replies) < page_size:
                break
            cursor = page.replies[-1].reply_no
        result = ReplyPage(replies=collected, total_count=total or len(collected))
        if with_children:
            for r in result.replies:
                if r.child_reply_count:
                    try:
                        r.children = self.child_replies(r.reply_no, limit=r.child_reply_count)
                    except FandingError:
                        r.children = []
        return result

    def iter_replies(
        self,
        post_no: int,
        limit: Optional[int] = None,
        sort: str = "recent",
        page_size: int = 50,
        my_comments_only: bool = False,
        max_pages: int = 200,
    ) -> Iterator[Reply]:
        page_size = max(1, min(int(page_size), MAX_REPLY_PAGE_SIZE))
        seen: set[int] = set()
        yielded = 0
        cursor: Optional[int] = None
        for _ in range(max_pages):
            page = self.reply_page(
                post_no,
                limit=page_size,
                sort=sort,
                after_reply_no=cursor,
                my_comments_only=my_comments_only,
            )
            if not page.replies:
                return
            new = 0
            for r in page.replies:
                if r.reply_no in seen:
                    continue
                seen.add(r.reply_no)
                new += 1
                yield r
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if new == 0 or len(page.replies) < page_size:
                return
            cursor = page.replies[-1].reply_no

    def comments(self, post_no: int, limit: int = 50, sort: str = "recent", **kw) -> ReplyPage:
        return self.replies(post_no, limit=limit, sort=sort, **kw)

    def tags(self, member_url: str) -> List[str]:
        member_url = self._normalize_member_url(member_url)
        data = self._request("/rest/post/tag_list", {"sMemberUrl": member_url})
        return list((data or {}).get("aTagList") or [])

    def channel_home(self, member_url: str) -> ChannelHome:
        member_url = self._normalize_member_url(member_url)
        data = self._request("/rest/channel/home", {"sMemberUrl": member_url})
        return ChannelHome.from_api(data or {})

    def channel_banners(self, member_url: str, tab: str = "home") -> List[Banner]:
        member_url = self._normalize_member_url(member_url)
        data = self._request(
            "/rest/channel/main_banner_list", {"sMemberUrl": member_url, "sTab": tab}
        )
        return [Banner.from_api(b) for b in ((data or {}).get("aMainBannerList") or [])]

    def channel_sections(
        self, member_url: str, tab: str = "home", after_section_no: Optional[int] = None
    ) -> List[ChannelSection]:
        member_url = self._normalize_member_url(member_url)
        data = self._request(
            "/rest/channel/section",
            {"sMemberUrl": member_url, "sTab": tab, "iLastSectionNo": after_section_no},
        )
        return [ChannelSection.from_api(s, client=self) for s in ((data or {}).get("aSectionList") or [])]

    def channel_feed(
        self, member_url: str, limit: int = MAX_FEED_PAGE_SIZE, after_post_no: Optional[int] = None
    ) -> FeedPage:
        member_url = self._normalize_member_url(member_url)
        data = self._request(
            "/rest/channel/feed",
            {
                "sMemberUrl": member_url,
                "iLimit": max(1, min(int(limit), MAX_FEED_PAGE_SIZE)),
                "iLastPostNo": after_post_no,
            },
        )
        return FeedPage.from_api(data or {}, client=self)

    def iter_channel_feed(
        self,
        member_url: str,
        limit: Optional[int] = None,
        page_size: int = MAX_FEED_PAGE_SIZE,
        max_pages: int = 500,
    ) -> Iterator[PostDetail]:
        page_size = max(1, min(int(page_size), MAX_FEED_PAGE_SIZE))
        seen: set[int] = set()
        yielded = 0
        cursor: Optional[int] = None
        for _ in range(max_pages):
            page = self.channel_feed(member_url, limit=page_size, after_post_no=cursor)
            if not page.posts:
                return
            new = 0
            for post in page.posts:
                if post.post_no in seen:
                    continue
                seen.add(post.post_no)
                new += 1
                yield post
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if new == 0 or len(page.posts) < page_size:
                return
            cursor = page.posts[-1].post_no

    def feed(self, member_url: str, limit: int = MAX_FEED_PAGE_SIZE) -> List[PostDetail]:
        return list(self.iter_channel_feed(member_url, limit=limit))

    def collection(self, collection_no: int) -> Collection:
        data = self._request("/rest/collection", {"iCollectionNo": int(collection_no)})
        col = Collection.from_api(data or {})
        col.collection_no = int(collection_no)
        return col

    def collection_posts(
        self,
        collection_no: int,
        limit: int = MAX_PAGE_SIZE,
        order: str = "asc",
        after_post_no: Optional[int] = None,
    ) -> PostPage:
        data = self._request(
            "/rest/collection/post_list",
            {
                "iCollectionNo": int(collection_no),
                "iLimit": max(1, min(int(limit), MAX_PAGE_SIZE)),
                "sOrderOption": order,
                "iLastPostNo": after_post_no,
            },
        )
        return PostPage.from_api(data or {}, client=self)

    def iter_collection_posts(
        self,
        collection_no: int,
        limit: Optional[int] = None,
        order: str = "asc",
        page_size: int = MAX_PAGE_SIZE,
        max_pages: int = 200,
    ) -> Iterator[Post]:
        page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        seen: set[int] = set()
        yielded = 0
        cursor: Optional[int] = None
        for _ in range(max_pages):
            page = self.collection_posts(
                collection_no, limit=page_size, order=order, after_post_no=cursor
            )
            if not page.posts:
                return
            new = 0
            for post in page.posts:
                if post.post_no in seen:
                    continue
                seen.add(post.post_no)
                new += 1
                yield post
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if new == 0 or len(page.posts) < page_size:
                return
            cursor = page.posts[-1].post_no

    def series_list(
        self,
        member_url: Optional[str] = None,
        creator_no: Optional[int] = None,
        limit: Optional[int] = None,
        page_size: int = 20,
    ) -> List[Collection]:
        return list(
            self.iter_series(
                member_url=member_url, creator_no=creator_no, limit=limit, page_size=page_size
            )
        )

    def iter_series(
        self,
        member_url: Optional[str] = None,
        creator_no: Optional[int] = None,
        limit: Optional[int] = None,
        page_size: int = 20,
        max_pages: int = 200,
    ) -> Iterator[Collection]:
        if creator_no is None:
            if not member_url:
                raise ValueError("member_url 또는 creator_no 가 필요합니다.")
            creator_no = self.channel(member_url).creator_no
        page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        seen: set[int] = set()
        yielded = 0
        cursor: Optional[int] = None
        for _ in range(max_pages):
            data = self._request(
                "/rest/collection/list",
                {
                    "iCreatorNo": int(creator_no),
                    "iLimit": page_size,
                    "iLastCollectionNo": cursor,
                },
            ) or {}
            rows = data.get("aCollectionList") or []
            if not rows:
                return
            new = 0
            for row in rows:
                col = Collection.from_api(row)
                if col.collection_no in seen:
                    continue
                seen.add(col.collection_no)
                new += 1
                yield col
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if new == 0 or len(rows) < page_size:
                return
            last_item = rows[-1]
            cursor = last_item.get("iNo") or last_item.get("iCollectionNo")

    def related_posts(self, post_no: int, limit: int = 12) -> List[Post]:
        data = self._request(
            "/rest/post/related_list", {"iPostNo": int(post_no), "iLimit": int(limit)}
        )
        return [Post.from_api(p, client=self) for p in ((data or {}).get("aPostList") or [])]

    def post_member_url(self, post_no: int) -> Optional[str]:
        data = self._request("/rest/post/member_url", {"iPostNo": int(post_no)})
        return (data or {}).get("sMemberUrl")

    def community_categories(self, member_url: str) -> List[CommunityCategory]:
        member_url = self._normalize_member_url(member_url)
        data = self._request("/rest/community/category", {"sCreatorUrl": member_url})
        return [CommunityCategory.from_api(c) for c in ((data or {}).get("aCategoryList") or [])]

    def community_posts(
        self,
        member_url: str,
        category_no: Optional[int] = None,
        page: int = 1,
        limit: int = 20,
        filter: Optional[str] = None,
    ) -> CommunityPage:
        member_url = self._normalize_member_url(member_url)
        data = self._request(
            "/rest/community/list",
            {
                "sCreatorUrl": member_url,
                "iCategoryNo": category_no,
                "iPageNo": max(1, int(page)),
                "iLimit": max(1, min(int(limit), MAX_COMMUNITY_PAGE_SIZE)),
                "sFilter": filter,
            },
        )
        return CommunityPage.from_api(data or {})

    def community_feed(
        self,
        member_url: str,
        limit: int = 20,
        after_community_no: Optional[int] = None,
        filter: Optional[str] = None,
    ) -> CommunityPage:
        member_url = self._normalize_member_url(member_url)
        data = self._request(
            "/rest/community/feed",
            {
                "sCreatorUrl": member_url,
                "iLimit": max(1, min(int(limit), MAX_COMMUNITY_PAGE_SIZE)),
                "iLastCommunityNo": after_community_no,
                "sFilter": filter,
            },
        )
        return CommunityPage.from_api(data or {})

    def iter_community_posts(
        self,
        member_url: str,
        limit: Optional[int] = None,
        page_size: int = MAX_COMMUNITY_PAGE_SIZE,
        filter: Optional[str] = None,
        max_pages: int = 500,
    ) -> Iterator[CommunityPost]:
        page_size = max(1, min(int(page_size), MAX_COMMUNITY_PAGE_SIZE))
        seen: set[int] = set()
        yielded = 0
        cursor: Optional[int] = None
        for _ in range(max_pages):
            page = self.community_feed(
                member_url, limit=page_size, after_community_no=cursor, filter=filter
            )
            if not page.posts:
                return
            new = 0
            for post in page.posts:
                if post.post_no in seen:
                    continue
                seen.add(post.post_no)
                new += 1
                yield post
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if new == 0 or len(page.posts) < page_size:
                return
            cursor = page.posts[-1].post_no

    def community_search(
        self,
        member_url: str,
        query: str,
        search_type: str = "title",
        sort: str = "recent",
        limit: int = 20,
        after_community_no: Optional[int] = None,
    ) -> CommunityPage:
        member_url = self._normalize_member_url(member_url)
        data = self._request(
            "/rest/community/search_list",
            {
                "sCreatorUrl": member_url,
                "iLimit": max(1, min(int(limit), MAX_COMMUNITY_PAGE_SIZE)),
                "iLastCommunityNo": after_community_no,
                "sSearchInput": query,
                "sSearchType": SEARCH_TYPE_ALIASES.get(str(search_type).lower(), search_type),
                "sSortOrder": sort,
            },
        )
        return CommunityPage.from_api(data or {})

    def tiers(self, creator: Any, invite_code: Optional[str] = None) -> MembershipTierList:
        creator_no = (
            int(creator) if isinstance(creator, int) or str(creator).isdigit()
            else self.channel(str(creator)).creator_no
        )
        data = self._request(
            "/rest/tier/list", {"iCreatorNo": int(creator_no), "sInviteCode": invite_code}
        )
        result = MembershipTierList.from_api(data or {})
        if result.creator is not None and not result.creator.creator_no:
            result.creator.creator_no = int(creator_no)
        return result

    def tier(self, tier_no: int) -> MembershipTier:
        data = self._request("/rest/tier", {"iTierNo": int(tier_no)}) or {}
        creator = Creator.from_api(data.get("aCreator")) if data.get("aCreator") else None
        return MembershipTier.from_api(data.get("aTierData") or {}, creator=creator)

    def search_creators(self, query: str, limit: Optional[int] = None) -> List[Creator]:
        data = self._request("/rest/search/preview", {"sSearchValue": query})
        rows = (data or {}).get("aSearchList") or []
        creators = [Creator.from_api(c) for c in rows]
        return creators[: int(limit)] if limit else creators

    def recommended_creators(self, limit: int = 5) -> List[Creator]:
        data = self._request("/rest/creator/recommend", {"iLimit": int(limit)})
        return [Creator.from_api(c) for c in (data or [])]

    def creator_count(self) -> int:
        data = self._request("/rest/creator/count")
        return int((data or {}).get("iCreatorCount") or 0)

    def explorer_tabs(self) -> Dict[str, Any]:
        return dict(self._request("/rest/explorer/tab_list") or {})

    def explorer_banners(self, tab_no: Optional[int] = None) -> List[Banner]:
        if tab_no is None:
            tab_no = (self.explorer_tabs().get("aHomeTab") or {}).get("iTabNo")
        data = self._request("/rest/explorer/banner_list", {"iTabNo": tab_no})
        return [Banner.from_api(b) for b in ((data or {}).get("aBannerList") or [])]

    def explorer_creators(
        self,
        main_category_no: Optional[int] = None,
        sub_category_no: Optional[Any] = None,
        keyword: Optional[str] = None,
        limit: int = 20,
        after_creator_no: Optional[int] = None,
    ) -> CreatorPage:
        data = self._request(
            "/rest/explorer/creator_list",
            {
                "iMainCategoryNo": main_category_no,
                "iSubCategoryNo": sub_category_no,
                "sSearchKeyword": keyword,
                "iLimit": max(1, min(int(limit), 50)),
                "iLastCreatorNo": after_creator_no,
            },
        )
        return CreatorPage.from_api(data or {})

    def hot_creators(self) -> List[HotCreatorGroup]:
        data = self._request("/rest/selection/hot_creator_list")
        return [HotCreatorGroup.from_api(g) for g in (data or [])]

    def selections(self, tab_no: Optional[int] = None, limit: int = 20) -> List[Dict[str, Any]]:
        data = self._request("/rest/selection/list", {"iTabNo": tab_no, "iLimit": int(limit)})
        return list((data or {}).get("aSelectionList") or [])

    def emoticons(self) -> List[Emoticon]:
        data = self._request("/rest/emoticon/list")
        return [Emoticon.from_api(e) for e in ((data or {}).get("aEmoticon") or [])]

    def reply_presets(self) -> List[ReplyPreset]:
        data = self._request("/rest/post_reply/preset_list")
        return [ReplyPreset.from_api(p) for p in ((data or {}).get("aPreset") or [])]

    def countries(self) -> List[Country]:
        data = self._request("/rest/phone/country_list")
        return [Country.from_api(c) for c in ((data or {}).get("aCountry") or [])]

    @staticmethod
    def _normalize_member_url(member_url: str) -> str:
        s = str(member_url).strip()
        s = re.sub(r"^https?://[^/]+", "", s)
        s = s.split("#", 1)[0].split("?", 1)[0].strip("/")
        if s.startswith("@"):
            s = s[1:]
        if "/" in s:
            s = s.split("/", 1)[0]
        if not s:
            raise ValueError("member_url 이 비어 있습니다.")
        return s

    def __repr__(self) -> str:
        return f"FandingClient(base_url={self.base_url!r})"


def _print_post_line(post: Post) -> None:
    when = post.published_at.strftime("%Y-%m-%d %H:%M") if post.published_at else "-"
    flags = " [paid]" if post.is_paid else ""
    if post.is_locked:
        flags += " [locked]"
    print(f"{post.post_no:>8}  {when}  조회 {post.view_count:>7}  {post.title}{flags}")


def _print_detail(cli: FandingClient, detail: PostDetail, with_text: bool, comments: int, comment_sort: str) -> None:
    when = detail.published_at.strftime("%Y-%m-%d %H:%M") if detail.published_at else "-"
    flags = []
    if detail.is_paid:
        flags.append("유료")
    if detail.is_locked:
        flags.append("잠김")
    if detail.is_adult_only:
        flags.append("성인")
    if detail.is_app_only:
        flags.append("앱전용")
    if detail.is_fixed_top:
        flags.append("고정")
    rule = "=" * 64
    print(rule)
    print(detail.title)
    print(rule)
    print(f"게시물 번호 : {detail.post_no}")
    print(f"주소        : {detail.url}")
    print(f"작성일      : {when}   상태: {detail.status or '-'}" + (f"   [{' / '.join(flags)}]" if flags else ""))
    print(f"작성자      : {detail.writer_nickname or detail.creator_nickname or '-'}")
    if detail.creator_thumbnail_url:
        print(f"작성자 사진 : {detail.creator_thumbnail_url}")
    print(f"반응        : 조회 {detail.view_count:,} · 좋아요 {detail.like_count:,} · 댓글 {detail.reply_count:,}")
    if detail.duration_text:
        print(f"영상 길이   : {detail.duration_text}")
    if detail.tags:
        print("태그        : " + ", ".join(detail.tags))
    if detail.collection:
        c = detail.collection
        order = c.get("iCollectionPostOrder")
        count = c.get("iPostCount")
        where = f" ({order}/{count})" if order and count else ""
        print(f"시리즈      : {c.get('sCollectionTitle')}{where}")
    if detail.thumbnail_url:
        print(f"썸네일      : {detail.thumbnail_url}")
    imgs = detail.images
    if imgs:
        print(f"이미지      : {len(imgs)}장")
        for u in imgs[:20]:
            print(f"              {u}")
        if len(imgs) > 20:
            print(f"              ... 외 {len(imgs) - 20}장")
    if detail.files:
        print(f"첨부 파일   : {len(detail.files)}개")
        for a in detail.files:
            extra = " ".join(x for x in (a.size_text, a.duration_text) if x)
            print(f"              [{a.file_type or '-'}] {a.display_name} {extra}".rstrip())
            if a.url:
                print(f"                {a.url}")
    if with_text:
        print("-" * 64)
        text = detail.text
        print(text if text else "(본문이 비어 있습니다. 잠긴 게시물일 수 있습니다.)")
    if comments:
        page = detail.comments(cli, limit=comments, sort=comment_sort, with_children=True)
        print("-" * 64)
        print(f"댓글 {page.total_count:,}개 중 {len(page.replies)}개")
        if not page.replies:
            print("(가져온 댓글이 없습니다. 잠긴 게시물이거나 댓글이 없습니다.)")
        for r in page.replies:
            marks = []
            if r.is_creator:
                marks.append("작성자")
            if r.is_fander:
                marks.append("팬딩")
            if r.is_fixed:
                marks.append("고정")
            mark = f" [{'/'.join(marks)}]" if marks else ""
            ts = r.published_at.strftime("%Y-%m-%d %H:%M") if r.published_at else "-"
            print(f"- {r.nickname}{mark}  {ts}  좋아요 {r.like_count}")
            for line in (r.content or "").splitlines() or [""]:
                print(f"    {line}")
            for child in r.children:
                cts = child.published_at.strftime("%Y-%m-%d %H:%M") if child.published_at else "-"
                cmark = " [작성자]" if child.is_creator else ""
                print(f"    └ {child.nickname}{cmark}  {cts}")
                for line in (child.content or "").splitlines() or [""]:
                    print(f"        {line}")
            if r.child_reply_count and not r.children:
                print(f"    (답글 {r.child_reply_count}개)")


def _print_related(cli: FandingClient, post_no: int, limit: int = 10) -> None:
    try:
        posts = cli.related_posts(post_no, limit=limit)
    except FandingError as e:
        print(f"(관련 게시물을 가져오지 못했습니다: {e})")
        return
    if not posts:
        return
    print("-" * 64)
    print(f"관련 게시물 {len(posts)}개")
    for p in posts:
        print(f"{p.post_no:>8}  {p.published_at:%Y-%m-%d}" if p.published_at else f"{p.post_no:>8}  ----.--.--", end="")
        print(f"  조회 {p.view_count:>7}  {p.title}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fanding",
        description="팬딩(fanding.kr) 비공식 클라이언트",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=argparse.SUPPRESS)
    parser.add_argument("--auth-token", default=None, help="인증 토큰 (Bearer)")
    parser.add_argument("--cookie", default=None, help="쿠키 헤더 문자열")
    parser.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")

    subparsers = parser.add_subparsers(dest="command", help="실행할 명령")

    p_ch = subparsers.add_parser("channel", help="채널 정보/메뉴/섹션 조회")
    p_ch.add_argument("member_url", help="채널 아이디 (예: test, @test)")
    p_ch.add_argument("--banners", action="store_true", help="채널 배너")
    p_ch.add_argument("--sections", default=None, choices=["home", "post", "event", "review"], help="탭별 섹션")
    p_ch.add_argument("--tags", action="store_true", help="태그 목록")

    p_posts = subparsers.add_parser("posts", help="채널 게시물 목록")
    p_posts.add_argument("member_url", help="채널 아이디")
    p_posts.add_argument("-n", "--limit", type=int, default=20, help="가져올 게시물 수")
    p_posts.add_argument("-s", "--sort", default="recent", help="정렬: recent | view | like")
    p_posts.add_argument("-q", "--search", default=None, help="검색어")
    p_posts.add_argument("-t", "--search-type", default="title", choices=["title", "content"], help="검색 대상")
    p_posts.add_argument("--text", action="store_true", help="첫 줄 본문 미리보기")

    p_post = subparsers.add_parser("post", help="게시물 상세 조회")
    p_post.add_argument("post_ref", help="게시물 번호 또는 URL")
    p_post.add_argument("--text", action="store_true", help="본문 전체 출력")
    p_post.add_argument("--comments", type=int, default=0, help="함께 가져올 댓글 수")
    p_post.add_argument("--comment-sort", default="recent", help="댓글 정렬: recent | popular")
    p_post.add_argument("--related", action="store_true", help="관련 게시물 출력")
    p_post.add_argument("--download", default=None, help="첨부파일을 다운로드할 폴더 경로")

    p_rep = subparsers.add_parser("replies", help="게시물 댓글 조회")
    p_rep.add_argument("post_no", type=int, help="게시물 번호")
    p_rep.add_argument("-n", "--limit", type=int, default=50, help="가져올 댓글 수")
    p_rep.add_argument("-s", "--sort", default="recent", help="정렬: recent | popular")
    p_rep.add_argument("--with-children", action="store_true", help="답글(대댓글) 포함")

    p_feed = subparsers.add_parser("feed", help="채널 피드 조회")
    p_feed.add_argument("member_url", help="채널 아이디")
    p_feed.add_argument("-n", "--limit", type=int, default=20, help="피드 수")

    p_series = subparsers.add_parser("series", help="시리즈 목록 조회")
    p_series.add_argument("member_url", help="채널 아이디")
    p_series.add_argument("-n", "--limit", type=int, default=20, help="시리즈 수")

    p_comm = subparsers.add_parser("community", help="커뮤니티 글 조회/검색")
    p_comm.add_argument("member_url", help="채널 아이디")
    p_comm.add_argument("-n", "--limit", type=int, default=20, help="가져올 글 수")
    p_comm.add_argument("-q", "--search", default=None, help="검색어")

    p_tiers = subparsers.add_parser("tiers", help="멤버십 플랜 목록")
    p_tiers.add_argument("member_url", help="채널 아이디 또는 번호")

    p_search = subparsers.add_parser("search", help="크리에이터 검색")
    p_search.add_argument("query", help="검색어")
    p_search.add_argument("-n", "--limit", type=int, default=10, help="결과 수")

    p_exp = subparsers.add_parser("explore", help="탐색 및 인기 크리에이터")
    p_exp.add_argument("--hot", action="store_true", help="인기 크리에이터만 출력")
    p_exp.add_argument("--emoticons", action="store_true", help="이모티콘 목록")

    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args_list = list(argv) if argv is not None else sys.argv[1:]

    if args_list and not args_list[0].startswith("-") and args_list[0] not in COMMANDS:
        first = args_list[0]
        if first.isdigit() or "/post/" in first or "@" in first and "/" in first:
            args_list = ["post", first] + args_list[1:]
        else:
            args_list = ["posts", first] + args_list[1:]
    elif args_list and args_list[0] in ("-p", "--post"):
        post_val = args_list[1] if len(args_list) > 1 else ""
        args_list = ["post", post_val] + args_list[2:]

    args = parser.parse_args(args_list)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    cli = FandingClient(base_url=args.base_url, auth_token=args.auth_token, cookies=args.cookie)

    try:
        if args.command == "channel":
            channel = cli.channel(args.member_url)
            if args.tags:
                tags = cli.tags(args.member_url)
                if args.json:
                    print(json.dumps(tags, ensure_ascii=False, indent=2))
                else:
                    print("\n".join(tags))
                return 0
            if args.banners:
                banners = cli.channel_banners(args.member_url)
                if args.json:
                    print(json.dumps([b.raw for b in banners], ensure_ascii=False, indent=2))
                else:
                    for b in banners:
                        print(f"{b.title or '-'} → {b.link_url or '-'}")
                return 0
            if args.sections:
                sections = cli.channel_sections(args.member_url, tab=args.sections)
                if args.json:
                    print(json.dumps([s.raw for s in sections], ensure_ascii=False, indent=2))
                else:
                    for s in sections:
                        print(f"[{s.section_type or '-'}] {s.title or ''} (총 {s.total_count:,}개)")
                return 0

            if args.json:
                print(json.dumps(channel.raw, ensure_ascii=False, indent=2))
            else:
                print(f"{channel.nickname} (@{channel.member_url}) [no={channel.creator_no}]")
                print(f"  URL : {channel.url}")
                if channel.one_line:
                    print(f"  소개 : {channel.one_line}")
                if channel.profile_image_url:
                    print(f"  사진 : {channel.profile_image_url}")
            return 0

        elif args.command == "posts":
            page = cli.post_list(
                args.member_url,
                limit=args.limit,
                sort=args.sort,
                search=args.search,
                search_type=args.search_type,
            )
            if args.json:
                print(json.dumps([p.raw for p in page], ensure_ascii=False, indent=2))
                return 0
            print(f"# @{args.member_url} 게시물 {len(page)}개 (전체 {page.total_count:,}개)")
            for p in page:
                _print_post_line(p)
                if args.text:
                    try:
                        lines = p.fetch().text.splitlines()
                        if lines:
                            print(f"    {lines[0][:100]}")
                    except Exception:
                        pass
            return 0

        elif args.command == "post":
            detail = cli.post_by_ref(args.post_ref)
            if args.download:
                saved = detail.download_attachments(args.download)
                print(f"첨부파일 {len(saved)}개 다운로드 완료: {args.download}")
            if args.json:
                print(json.dumps(detail.raw, ensure_ascii=False, indent=2, default=str))
            else:
                _print_detail(cli, detail, args.text, args.comments, args.comment_sort)
                if args.related:
                    _print_related(cli, detail.post_no)
            return 0

        elif args.command == "replies":
            page = cli.replies(
                args.post_no,
                limit=args.limit,
                sort=args.sort,
                with_children=args.with_children,
            )
            if args.json:
                print(json.dumps(page.raw, ensure_ascii=False, indent=2))
            else:
                print(f"# 게시물 {args.post_no} 댓글 {len(page)}개 (총 {page.total_count:,}개)")
                for r in page:
                    print(f"- {r.nickname} (좋아요 {r.like_count}): {r.content}")
                    for ch in r.children:
                        print(f"    └ {ch.nickname}: {ch.content}")
            return 0

        elif args.command == "feed":
            posts = list(cli.iter_channel_feed(args.member_url, limit=args.limit))
            if args.json:
                print(json.dumps([p.raw for p in posts], ensure_ascii=False, indent=2))
            else:
                print(f"# @{args.member_url} 피드 {len(posts)}개")
                for p in posts:
                    print(f"{p.post_no:>8}  {p.title} (조회 {p.view_count:,} · 좋아요 {p.like_count:,})")
            return 0

        elif args.command == "series":
            series = cli.series_list(args.member_url, limit=args.limit)
            if args.json:
                print(json.dumps([s.raw for s in series], ensure_ascii=False, indent=2))
            else:
                print(f"# @{args.member_url} 시리즈 {len(series)}개")
                for s in series:
                    print(f"{s.collection_no:>8}  ({s.post_count:>3}편)  {s.title}")
            return 0

        elif args.command == "community":
            if args.search:
                page = cli.community_search(args.member_url, args.search, limit=args.limit)
            else:
                page = cli.community_feed(args.member_url, limit=args.limit)
            if args.json:
                print(json.dumps(page.raw, ensure_ascii=False, indent=2))
            else:
                print(f"# 커뮤니티 글 {len(page)}개 (총 {page.total_count:,}개)")
                for p in page:
                    print(f"{p.post_no:>8}  {p.nickname:<12} {p.title or p.text[:40]}")
            return 0

        elif args.command == "tiers":
            res = cli.tiers(args.member_url)
            if args.json:
                print(json.dumps(res.raw, ensure_ascii=False, indent=2))
            else:
                who = res.creator.nickname if res.creator else args.member_url
                print(f"# {who} 멤버십 플랜 {len(res.tiers)}개")
                for t in res:
                    price = f"{t.price:,}원" if t.price else "-"
                    print(f"{t.tier_no:>7}  {price:>10}  {t.name}")
            return 0

        elif args.command == "search":
            found = cli.search_creators(args.query, limit=args.limit)
            if args.json:
                print(json.dumps([c.raw for c in found], ensure_ascii=False, indent=2))
            else:
                print(f"# 크리에이터 검색 '{args.query}' ({len(found)}명)")
                for c in found:
                    print(f"{c.creator_no:>8}  @{(c.member_url or '-'):<20} {c.nickname}")
            return 0

        elif args.command == "explore":
            if args.emoticons:
                items = cli.emoticons()
                if args.json:
                    print(json.dumps([e.raw for e in items], ensure_ascii=False, indent=2))
                else:
                    for e in items:
                        print(f"{e.emoticon_no:>4}  {e.image_url}")
                return 0
            if args.hot:
                groups = cli.hot_creators()
                if args.json:
                    print(json.dumps([g.raw for g in groups], ensure_ascii=False, indent=2))
                else:
                    for g in groups:
                        print(f"# 카테고리 {g.category_no} ({len(g.creators)}명)")
                        for c in g.creators:
                            print(f"  {c.creator_no:>8}  @{(c.member_url or '-'):<20} {c.nickname}")
                    return 0

            tabs = cli.explorer_tabs()
            if args.json:
                print(json.dumps(tabs, ensure_ascii=False, indent=2))
            else:
                home_tab = (tabs.get("aHomeTab") or {}).get("aTabNameLang", {}).get("ko")
                print(f"# 탐색 홈: {home_tab}")
                print(f"- 전체 크리에이터: {cli.creator_count():,}명")
            return 0

    except FandingError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
