#!/usr/bin/env python3
"""
GitHub File Uploader
Uploads files from outputs to GitHub repository
"""

import os
import sys
import json
import base64
import requests

def upload_to_github(
    filepath: str,
    repo_owner: str,
    repo_name: str,
    token: str,
    branch: str = "main",
    commit_message: str = None
):
    """
    Upload a file to GitHub repository.
    
    Args:
        filepath: Local file path (e.g., /mnt/user-data/outputs/V60_fixed.py)
        repo_owner: GitHub username
        repo_name: Repository name
        token: GitHub personal access token
        branch: Branch name (default: main)
        commit_message: Commit message (auto-generated if None)
    
    Returns:
        dict with status and commit URL
    """
    
    # Read file content
    try:
        with open(filepath, 'rb') as f:
            content = base64.b64encode(f.read()).decode('utf-8')
    except FileNotFoundError:
        return {
            "status": "error",
            "error": f"File not found: {filepath}"
        }
    
    # Extract filename from path
    filename = os.path.basename(filepath)
    
    # Auto-generate commit message if not provided
    if not commit_message:
        commit_message = f"Add {filename} via Claude"
    
    # GitHub API URL
    api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{filename}"
    
    # Headers
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # Check if file exists (to get SHA for update)
    sha = None
    try:
        response = requests.get(api_url, headers=headers, params={"ref": branch})
        if response.status_code == 200:
            sha = response.json()["sha"]
            print(f"File exists, updating... (SHA: {sha[:7]})", file=sys.stderr)
        else:
            print("Creating new file...", file=sys.stderr)
    except Exception as e:
        print(f"Assuming new file (check failed: {e})", file=sys.stderr)
    
    # Prepare payload
    payload = {
        "message": commit_message,
        "content": content,
        "branch": branch
    }
    
    if sha:
        payload["sha"] = sha
    
    # Upload
    try:
        response = requests.put(api_url, headers=headers, json=payload)
        response.raise_for_status()
        
        result = response.json()
        commit_url = result["commit"]["html_url"]
        
        return {
            "status": "success",
            "filename": filename,
            "commit_url": commit_url,
            "sha": result["content"]["sha"][:7]
        }
    
    except requests.exceptions.HTTPError as e:
        error_detail = ""
        try:
            error_detail = response.json().get("message", "")
        except:
            error_detail = response.text
        
        return {
            "status": "error",
            "error": str(e),
            "detail": error_detail,
            "status_code": response.status_code
        }

def main():
    if len(sys.argv) < 5:
        print("Usage: upload_github.py <filepath> <owner> <repo> <token> [branch] [message]")
        sys.exit(1)
    
    filepath = sys.argv[1]
    owner = sys.argv[2]
    repo = sys.argv[3]
    token = sys.argv[4]
    branch = sys.argv[5] if len(sys.argv) > 5 else "main"
    message = sys.argv[6] if len(sys.argv) > 6 else None
    
    result = upload_to_github(filepath, owner, repo, token, branch, message)
    print(json.dumps(result, indent=2))
    
    if result["status"] == "error":
        sys.exit(1)

if __name__ == "__main__":
    main()
