import os
import uvicorn
from server.app import build_default_app

if __name__ == "__main__":
    # 기본값은 안전한 127.0.0.1(로컬 전용). 레일웨이 등 외부 접속이 필요한 배포 환경에서는
    # HOST=0.0.0.0 을 주입한다(예: railway.toml / Dockerfile).
    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run(build_default_app(), host=host, port=int(os.environ.get("PORT", "8000")))
