#!/usr/bin/env python3
# /// script
# dependencies = ["openai>=1.0.0"]
# ///
"""Second opinion code reviewer using GitHub Models API (GPT-4o)."""

import os
import subprocess
import sys


def get_github_token() -> str:
    if token := os.environ.get("GITHUB_TOKEN"):
        return token
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def review_files(paths: list[str]) -> str:
    from openai import OpenAI

    blocks = []
    for path in paths:
        with open(path) as f:
            blocks.append(f"### {path}\n```\n{f.read()}\n```")

    content = "\n\n".join(blocks)

    client = OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=get_github_token(),
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior software engineer providing a second-opinion code review. "
                    "Focus on: correctness bugs, security issues, performance problems, and design concerns. "
                    "Be concise and specific. Reference file names and line numbers where possible. "
                    "Respond in Japanese."
                ),
            },
            {
                "role": "user",
                "content": f"以下のコードをレビューしてください:\n\n{content}",
            },
        ],
        temperature=0.3,
    )

    return response.choices[0].message.content


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: second_opinion.py <file1> [file2 ...]", file=sys.stderr)
        sys.exit(1)

    print(review_files(sys.argv[1:]))
