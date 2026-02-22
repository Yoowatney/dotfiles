"""YouTube video summarizer using Gemini API."""

import argparse
import os
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(os.path.expanduser("~/.config/gemini/.env"))


def summarize(url: str, prompt: str | None = None) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not set in ~/.config/gemini/.env", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    default_prompt = (
        "이 영상을 한국어로 요약해줘. "
        "핵심 내용을 구조화해서 정리하고, "
        "중요한 기술적 세부사항이 있으면 포함해줘."
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=types.Content(
            parts=[
                types.Part(
                    file_data=types.FileData(file_uri=url)
                ),
                types.Part(text=prompt or default_prompt),
            ]
        ),
    )
    return response.text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Summarize a YouTube video using Gemini")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("-p", "--prompt", help="Custom prompt (default: Korean summary)")
    args = parser.parse_args()

    print(summarize(args.url, args.prompt))
