# Python script to migrate repositories from GitLab to GitHub.
#
# Prerequisites:
# 1. Python 3 installed.
# 2. Git command-line tool installed and in your system's PATH.
# 3. The 'requests' library for Python. If you don't have it, run:
#    pip install requests

import requests
import subprocess
import os
import shutil
import dotenv

# --- CONFIGURATION: PLEASE FILL THESE VALUES OUT ---

# 1. Your school's GitLab instance URL (e.g., "https://gitlab.yourschool.edu")
GITLAB_URL = "https://gitlab.lnu.se" # <-- CHANGE THIS

# 2. Your GitLab Personal Access Token.
#    - Go to GitLab -> User Settings -> Access Tokens.
#    - Create a token with the 'read_api' scope.
GITLAB_PRIVATE_TOKEN = dotenv.get_key("ACCESS_TOKEN") # <-- CHANGE THIS

# 3. Your GitLab username.
GITLAB_USERNAME = dotenv.get_key("GITLAB_USERNAME") # <-- CHANGE THIS

# 4. Your GitHub username.
GITHUB_USERNAME = dotenv.get_key("GITHUB_USERNAME") # <-- CHANGE THIS

# 5. Your GitHub Personal Access Token.
#    - Go to GitHub -> Settings -> Developer settings -> Personal access tokens.
#    - Generate a new token with the 'repo' scope.
GITHUB_TOKEN = dotenv.get_key("GITHUB_TOKEN") # <-- CHANGE THIS

# 6. Set to True to make new GitHub repos private, False for public.
CREATE_PRIVATE_REPOS = False

# --- END OF CONFIGURATION ---


def get_gitlab_projects():
    """Fetches a list of all projects for the configured user from GitLab."""
    print(f"Fetching projects for GitLab user '{GITLAB_USERNAME}'...")
    
    # GitLab API endpoint for user projects
    api_url = f"{GITLAB_URL}/api/v4/users/{GITLAB_USERNAME}/projects"
    headers = {"PRIVATE-TOKEN": GITLAB_PRIVATE_TOKEN}
    params = {"per_page": 100} # Get up to 100 projects per request

    try:
        response = requests.get(api_url, headers=headers, params=params)
        response.raise_for_status()  # Raises an exception for bad status codes (4xx or 5xx)
        projects = response.json()
        print(f"Successfully found {len(projects)} projects.")
        return projects
    except requests.exceptions.RequestException as e:
        print(f"Error fetching projects from GitLab: {e}")
        # Try to print more specific error from GitLab if available
        try:
            error_data = e.response.json()
            if 'message' in error_data:
                print(f"GitLab API Error: {error_data['message']}")
        except (ValueError, AttributeError):
            pass # Ignore if response is not JSON or doesn't have a message
        return None


def create_github_repo(project_name, description):
    """Creates a new repository on GitHub."""
    print(f"Creating GitHub repository '{project_name}'...")
    
    api_url = "https://api.github.com/user/repos"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "name": project_name,
        "description": description,
        "private": CREATE_PRIVATE_REPOS
    }

    try:
        response = requests.post(api_url, headers=headers, json=data)
        # 422 Unprocessable Entity usually means the repo already exists
        if response.status_code == 422:
            print(f"Repository '{project_name}' may already exist on GitHub. Will attempt to push anyway.")
            # Construct the URL manually as we assume it exists
            return f"https://github.com/{GITHUB_USERNAME}/{project_name}.git"
            
        response.raise_for_status()
        repo_data = response.json()
        print(f"Successfully created GitHub repository.")
        return repo_data["clone_url"]
    except requests.exceptions.RequestException as e:
        print(f"Error creating GitHub repository '{project_name}': {e}")
        return None


def mirror_repository(gitlab_clone_url, github_clone_url):
    """Clones a repo from GitLab and mirrors it to GitHub."""
    repo_name = gitlab_clone_url.split("/")[-1]
    local_path = repo_name
    
    # Clean up any previous attempt
    if os.path.exists(local_path):
        print(f"Removing existing local directory '{local_path}'...")
        shutil.rmtree(local_path)

    print(f"1. Mirroring '{repo_name}' from GitLab...")
    try:
        # Use token in URL for authentication in CI/CD environments if needed
        authenticated_gitlab_url = gitlab_clone_url.replace("https://", f"https://oauth2:{GITLAB_PRIVATE_TOKEN}@")
        subprocess.run(["git", "clone", "--mirror", authenticated_gitlab_url], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: Failed to clone from GitLab.")
        print(f"  Git Command Output:\n{e.stderr}")
        return False

    # The cloned directory has a .git suffix
    local_mirror_path = f"{repo_name}.git"
    os.chdir(local_mirror_path)

    print(f"2. Pushing mirror to GitHub...")
    try:
        authenticated_github_url = github_clone_url.replace("https://", f"https://{GITHUB_USERNAME}:{GITHUB_TOKEN}@")
        subprocess.run(["git", "push", "--mirror", authenticated_github_url], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: Failed to push to GitHub.")
        print(f"  Git Command Output:\n{e.stderr}")
        os.chdir("..") # Go back before returning
        return False
    
    # Go back to the parent directory and clean up
    os.chdir("..")
    shutil.rmtree(local_mirror_path)
    print(f"Successfully mirrored '{repo_name}'.\n")
    return True


def main():
    """Main function to run the migration."""
    print("--- Starting GitLab to GitHub Migration Script ---")
    
    # Basic validation
    if "your_gitlab_token" in GITLAB_PRIVATE_TOKEN or "your_github_token" in GITHUB_TOKEN:
        print("\nFATAL ERROR: Please fill out the configuration variables at the top of the script.")
        return

    gitlab_projects = get_gitlab_projects()

    if not gitlab_projects:
        print("No projects found or an error occurred. Exiting.")
        return

    successful_migrations = 0
    failed_migrations = []

    for project in gitlab_projects:
        project_name = project["path"]
        project_description = project.get("description", "")
        gitlab_clone_url = project["http_url_to_repo"]

        print(f"--- Processing project: {project_name} ---")

        # 1. Create the repository on GitHub
        github_clone_url = create_github_repo(project_name, project_description)

        if not github_clone_url:
            print(f"Skipping migration for '{project_name}' due to GitHub creation failure.\n")
            failed_migrations.append(project_name)
            continue
        
        # 2. Mirror the repository contents
        success = mirror_repository(gitlab_clone_url, github_clone_url)
        if success:
            successful_migrations += 1
        else:
            failed_migrations.append(project_name)

    print("--- Migration Complete ---")
    print(f"Successfully migrated: {successful_migrations} repositories.")
    if failed_migrations:
        print(f"Failed to migrate: {len(failed_migrations)} repositories:")
        for name in failed_migrations:
            print(f"  - {name}")
    print("--------------------------")


if __name__ == "__main__":
    main()

