import json
import sys
from urllib.request import Request, urlopen


def main() -> None:
    api_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    request = Request(f"{api_url}/api/v1/demo/seed", method="POST")
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

