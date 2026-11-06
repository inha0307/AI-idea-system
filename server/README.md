# CrackTwin 서버 (FastAPI)

자세한 약속(API 형식, DB 구조, 좌표 규칙)은 [`docs/시스템팀_개발.md`](../docs/시스템팀_개발.md)를 따릅니다.

## 처음 한 번

```powershell
cd server
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt          # 실제 모델을 쓸 때는 requirements-ai.txt
Copy-Item .env.example .env              # 값 확인 (SEVERITY_* 는 AI 담당이 확정)
docker compose up -d                     # PostgreSQL
python seed.py                           # 시연 데이터 (기존 데이터 삭제됨)
# python seed.py --no-cracks             # 구조물만, 예시 균열 없이 (실제 시연 전 초기화)
```

## 실행

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- API 문서: http://localhost:8000/docs
- 상태 확인: http://localhost:8000/health

## 시연 한 번에 켜기·끄기

```powershell
# 저장소 최상위 폴더에서
powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1   # DB → API → 웹 → 시그널링 → UE5 → 터널
powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1    # 모두 종료 (DB 데이터는 유지)
```

- 시그널링 서버와 UE5 경로는 `scripts\.env.example`을 `scripts\.env`로 복사해 UE5 담당이 채웁니다. 비어 있으면 그 단계는 건너뜁니다.
- `-NoTunnel`: 터널 없이 실행 / `-NoWeb`: 웹 없이 실행
- 터널 주소(`https://…trycloudflare.com`)는 실행할 때마다 바뀌므로 화면에 나온 주소를 프론트 담당에게 전달합니다.

## 탐지 모드 (`.env`의 `DETECTOR`)

| 값 | 동작 |
|---|---|
| `fake` | 이미지와 상관없이 고정된 균열 1개를 돌려줌 (M2 연결 테스트용) |
| `yolo` | `MODEL_PATH`의 `best.pt`로 실제 탐지. 파일이 없으면 서버가 시작되지 않음 |

**`yolo` 모드 설치** (서버를 끈 상태에서. 켜져 있으면 cv2 파일이 잠겨 실패합니다)

```powershell
pip install -r requirements-ai.txt
pip install --no-deps ultralytics==8.4.203     # opencv-python을 함께 깔지 않도록 따로 설치
```

- `best.pt`는 Drive `/models`에서 받아 `server/models/`에 둡니다. `.glb`는 Drive `/assets`에서 받아 `server/storage/assets/structure.glb`에 둡니다.
- **분할(seg) 모델**이면 모델이 준 균열 마스크로 길이·폭을 잽니다.
- **탐지(detect) 모델**(박스만 줌)이면 박스 안에서 주변보다 어둡고 길게 이어진 선을 골라 마스크를 만듭니다 (`detector.mask_from_box`). 선을 못 찾으면 박스 대각선을 1픽셀 폭으로 처리해, 웹에서 「측정 신뢰 불가」로 표시되게 합니다.
- 2026-11-06 기준 `best.pt`: 탐지(detect) 모델, 클래스 1개(균열), ultralytics 8.4.203, imgsz 640, 50 epoch. CPU 추론 약 0.1초/장.

## 날씨 (`GET /structures/{id}/weather`)

- 기상청 초단기실황(공공데이터포털 「기상청_단기예보 조회서비스」)으로 구조물 위치의 기온·습도·풍속을 돌려줍니다.
- 키는 `.env`의 `KMA_SERVICE_KEY`에 넣습니다 (Git 금지). 없으면 이 API만 503이고 나머지는 정상입니다.
- 구조물의 위경도(`latitude`, `longitude`)는 `seed.py`에서 넣습니다. 서버가 기상청 격자(nx, ny)로 바꿔 조회합니다.

## 테스트 (Docker 없이 SQLite로 실행)

```powershell
pip install -r requirements-dev.txt
python -m pytest tests -q
```

## 구조

```text
app/
├─ main.py            # 앱 생성, CORS, /files, 시작 시 테이블·모델 준비
├─ config.py          # .env 읽기 (상대 경로는 server/ 기준)
├─ db.py, models.py   # SQLAlchemy (structures, inspections, cracks)
├─ schemas.py         # API 입출력 (camelCase)
├─ images.py          # 이미지 읽기·저장·결과 그리기
├─ ai/detector.py     # AI 담당 관리: load_model(), detect()
└─ routers/           # health, structures, inspections, cracks
```

## `detect` 오류 응답

| 상태 | 경우 |
|---|---|
| 404 | 없는 점검 번호 |
| 422 | `meta` 형식 오류, `hitOk=false`, `meta.structureId`가 점검의 구조물과 다름, 이미지를 읽을 수 없음 |

## 참고

- 테이블은 서버 시작 시 없는 것만 만들어집니다. 컬럼을 바꾸면 `python seed.py`로 다시 만듭니다.
- 같은 점검에서 같은 캡처 `id`를 다시 보내면 이미지 파일은 덮어쓰고 균열 행은 새로 추가됩니다.
