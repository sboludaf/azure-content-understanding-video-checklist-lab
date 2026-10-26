"""Azure entrypoint; private operational config arrives through App Service settings."""
import json
import os
from pathlib import Path


def main():
    raw = os.getenv("MVP_AUTHORIZATION_JSON")
    if raw:
        auth = json.loads(raw)
        path = Path(os.environ["MVP_AUTHORIZATION_PATH"])
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as stream:
            stream.write(json.dumps(auth, ensure_ascii=False))
        tmp.chmod(0o600)
        os.replace(tmp, path)
    import uvicorn
    uvicorn.run("video_checklist.main:app", host="0.0.0.0", port=8000, workers=1, access_log=False)


if __name__ == "__main__":
    main()
