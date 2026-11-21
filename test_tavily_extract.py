"""Try Tavily Basic Extract once on the Lulus homepage."""
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

URL = "https://lulus.com"


def main():
    load_dotenv(Path(__file__).resolve().with_name(".env"))
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        print("Set TAVILY_API_KEY in your local .env file.")
        return 1

    try:
        response = httpx.post(
            "https://api.tavily.com/extract",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "urls": [URL],
                "extract_depth": "basic",
                "format": "text",
                "include_usage": True,
            },
            timeout=25,
        )
        report = response.json()
    except httpx.RequestError as error:
        print(f"Result: request failed ({type(error).__name__}).")
        print("Characters extracted: 0")
        print("Credits reported: not reported")
        return 1
    except ValueError:
        print(f"Result: Tavily returned non-JSON data (HTTP {response.status_code}).")
        print("Characters extracted: 0")
        print("Credits reported: not reported")
        return 1

    usage = report.get("usage") or {}
    credits = usage.get("credits", "not reported")
    text = next(
        (item["raw_content"].strip() for item in report.get("results", [])
         if item.get("raw_content") and item["raw_content"].strip()),
        "",
    )
    if not response.is_success:
        print(f"Result: Tavily returned HTTP {response.status_code}.")
    elif text:
        print("Result: Tavily returned text. Check the preview for homepage content.")
        print("Preview:", " ".join(text.split())[:350])
    else:
        failures = report.get("failed_results") or []
        detail = failures[0].get("error") if failures else None
        detail = str(detail or "Tavily returned no usable text.").replace(key, "[redacted]")
        print(f"Result: {detail}")
    print(f"Characters extracted: {len(text)}")
    print(f"Credits reported: {credits}")
    return 0 if response.is_success and text else 1


if __name__ == "__main__":
    raise SystemExit(main())
