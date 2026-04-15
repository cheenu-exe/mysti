# Mystiv Backend

This backend powers the command-console UI in `index.html`.

## Run

```powershell
cd backend
uvicorn main:app --reload
```

You can also start it from the project root:

```powershell
uvicorn main:app --reload
```

## What It Includes

- `GET /api/health`
- `POST /api/mode`
- `GET /api/suggestions`
- `POST /api/quickops`
- `POST /api/chat`
- `POST /api/voice`
- `GET /api/state` as a Server-Sent Events stream

## Quick Verification

```powershell
python -m unittest backend.tests.test_api
```
