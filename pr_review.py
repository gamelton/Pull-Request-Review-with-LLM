#!/usr/bin/env python3

# LLM review Pull Request

import requests
import re
from datetime import datetime, timedelta

# Constants
BITBUCKET_API_TOKEN = "<your-bitbucket-token>"
BITBUCKET_ALL_PR_URL = "https://bitbucket.example.com/rest/api/latest/projects/<project name>/repos/<repository>/pull-requests"
OLLAMA_URL = "http://ollama.example.com:11434/api/chat"

## All PR
response = requests.get(BITBUCKET_ALL_PR_URL, headers={"Authorization": f"Bearer {BITBUCKET_API_TOKEN}", "Accept": "application/json;charset=UTF-8"}, params={"state": "OPEN", "limit": 99})
response.raise_for_status()

now = datetime.now()
two_hours_ago = now - timedelta(hours=2)

recent_pr_ids = []
if response and response.json()["values"]:
    recent_pr_ids = [pr["id"] for pr in response.json()["values"] if datetime.fromtimestamp(pr["createdDate"] / 1000) > two_hours_ago]
    
    # PR diff
    for pr in recent_pr_ids:
        BITBUCKET_PR_ID = pr
        BITBUCKET_PR_DIFF_URL = f"https://bitbucket.example.com/rest/api/latest/projects/<project name>/repos/<repository>/pull-requests/{BITBUCKET_PR_ID}.diff"
        response = None
        response = requests.get(BITBUCKET_PR_DIFF_URL, headers={"Authorization": f"Bearer {BITBUCKET_API_TOKEN}"})
        response.raise_for_status()
        git_diff = response.text
        
        # LLM review of the diff
        llm_request = f"Given the following git diff of an Ansible pull request, identify and list any issues found: Broken Code, Syntax Errors, Duplicate Code, Null Variables, Unused Code, Mutable Existence, Code Optimization, Confusing Code.  \nReview and provide a list of any issues found, being clear, simple, and concise in your assessment. List only detected issues. Provide brief descriptions and locations where each issue occurs.  \n{git_diff}"
        
        data = {"model": "deepseek-r1:14b", "stream": False, "messages": [{ "role": "user", "content": f"{llm_request}" }], "options": { "num_ctx": 8192 }}
        
        response = None
        response = requests.post(OLLAMA_URL, json=data, headers={"Content-Type": "application/json"})
        response.raise_for_status()
        if response and response.json()["message"]["content"]:
            llm_review_raw = response.json()["message"]["content"]
            llm_review = re.sub(r"<think>.*?</think>\n?", "", llm_review_raw, flags=re.DOTALL) 
            
            # PR comment
            BITBUCKET_PR_COMMENTS_URL = f"https://bitbucket.example.com/rest/api/latest/projects/<project name>/repos/<repository>/pull-requests/{BITBUCKET_PR_ID}/comments"
            data = {"text": f"Please note that this comment is generated by an AI model and may not be fully accurate or reliable.  \n{llm_review}"}
            response = requests.request("POST", BITBUCKET_PR_COMMENTS_URL, json=data, headers={"Authorization": f"Bearer {BITBUCKET_API_TOKEN}", "Accept": "application/json;charset=UTF-8", "Content-Type": "application/json"})
            response.raise_for_status()
