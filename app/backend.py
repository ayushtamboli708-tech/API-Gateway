from fastapi import FastAPI, Request

app = FastAPI()

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def backend(path: str, request: Request):
    return {
        "message": "Hello from Backend",
        "path": path,
        "method": request.method,
        "request_id": request.headers.get("X-Request-ID"),
    }