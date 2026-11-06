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
```

## 실행

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- API 문서: http://localhost:8000/docs
- 상태 확인: http://localhost:8000/health

## 탐지 모드 (`.env`의 `DETECTOR`)

| 값 | 동작 |
|---|---|
| `fake` | 이미지와 상관없이 고정된 균열 1개를 돌려줌 (M2 연결 테스트용) |
| `yolo` | `MODEL_PATH`의 `best.pt`로 실제 탐지. `pip install -r requirements-ai.txt` 필요. 파일이 없으면 서버가 시작되지 않음 |

`best.pt`는 Drive `/models`에서 받아 `server/models/`에 둡니다. `.glb`는 Drive `/assets`에서 받아 `server/storage/assets/structure.glb`에 둡니다.

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
