import uvicorn
from server.app import build_default_app

if __name__ == "__main__":
    uvicorn.run(build_default_app(), host="127.0.0.1", port=8000)
