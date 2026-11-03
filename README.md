# Fanding Unofficial Python Client (`fanding.py`)

팬딩(fanding.kr)의 채널 정보, 게시물, 댓글, 시리즈, 커뮤니티, 멤버십 티어 조회를 지원하는 비공식 파이썬 클라이언트 및 CLI 도구입니다.

##  CLI 사용법
 
터미널에서 `fanding.py`를 직접 실행하여 데이터를 조회할 수 있습니다.

```bash
# 1. 채널 정보 조회
python fanding.py channel test
python fanding.py channel @test --banners
python fanding.py channel test --tags
python fanding.py channel test --sections home

# 2. 게시물 목록 조회
python fanding.py posts test -n 10 --sort recent
python fanding.py posts test -q "공지" -t title
python fanding.py posts test --text   # 본문 첫 줄 미리보기

# 3. 단일 게시물 상세 조회
python fanding.py post 12345 --text
python fanding.py post "https://fanding.kr/@test/post/12345" --comments 10
python fanding.py post 12345 --download ./downloads  # 첨부파일 다운로드

# 4. 댓글 및 대댓글 조회
python fanding.py replies 12345 -n 20 --sort recent --with-children

# 5. 채널 피드 및 시리즈 조회
python fanding.py feed test -n 10
python fanding.py series test

# 6. 커뮤니티 글 조회 및 검색
python fanding.py community test -n 20
python fanding.py community test -q "팬아트"

# 7. 멤버십 티어 플랜 목록
python fanding.py tiers test

# 8. 크리에이터 검색 및 탐색
python fanding.py search "test"
python fanding.py explore --hot
python fanding.py explore --emoticons

# 9. JSON 원본 출력 (모든 명령에 --json 플래그 지원)
python fanding.py channel test --json
python fanding.py post 12345 --json
```

### 인증 옵션 (비공개 / 유료 / 멤버십 전용)

로그인이 필요한 게시물이나 첨부파일을 조회할 때는 쿠키나 인증 토큰을 전달할 수 있습니다.

```bash
python fanding.py post 12345 --cookie "connect.sid=...; ..."
python fanding.py post 12345 --auth-token "eyJhbGciOi..."
```

---

## Python SDK 사용법

스크립트나 애플리케이션 내에서 `FandingClient`를 가져와 사용할 수 있습니다.

```python
from fanding import FandingClient, ChannelNotFound, PostNotFound

# 클라이언트 초기화
client = FandingClient(
    # auth_token="...",    # 선택사항: Bearer 토큰
    # cookies="...",       # 선택사항: 세션 쿠키
    timeout=15.0,
    retries=2,
    delay=0.1,             # API 호출 간 딜레이 (초)
)

# 채널 정보 가져오기
try:
    channel = client.channel("test")
    print(channel.nickname, channel.one_line)
except ChannelNotFound:
    print("채널이 존재하지 않습니다.")

# 게시물 목록 (리스트 반환)
posts = client.posts("test", limit=10, sort="recent")
for p in posts:
    print(p.post_no, p.title, p.view_count)

# 대량 게시물 순회 (제너레이터)
for post in client.iter_posts("test", limit=100):
    print(f"[{post.post_no}] {post.title}")

# 게시물 상세 조회 및 첨부파일 다운로드
detail = client.post(12345)
print("본문 내용:", detail.text)
print("이미지 URL:", detail.images)
detail.download_attachments("./downloads")

# 댓글 조회
replies = client.replies(12345, limit=50, with_children=True)
for r in replies:
    print(f"{r.nickname}: {r.content}")
    for child in r.children:
        print(f"  └ {child.nickname}: {child.content}")
```

---

## 디렉터리 구성

- `fanding.py`: 클라이언트 및 CLI 실행 통합 모듈 (주석 제거 및 성능/호환성 개선 완료)
- `readme.md`: 사용 설명서
- `example/`: SDK 기능별 예제 코드
  - `01_channel_info.py`: 채널 프로필, 멤버십 티어, 태그 조회 예제
  - `02_fetch_posts.py`: 게시물 목록 조회 및 대량 페이지네이션 순회 예제
  - `03_download_attachments.py`: 게시물 상세 정보 파싱 및 첨부파일 일괄 다운로드 예제
  - `04_community_and_replies.py`: 커뮤니티 글 검색 및 댓글/대댓글 트리 조회 예제

---

## ⚙️ 요구사항

- Python 3.9 이상
- 별도 외부 패키지 설치 필요 없음 (표준 라이브러리 기반)
