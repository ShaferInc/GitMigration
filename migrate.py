import requests
import subprocess
import os
import shutil
import stat # Import the stat module
from dotenv import load_dotenv

# --- SETUP ---
# This will load variables from a file named '.env' in the same directory.
# Your .env file should look like this:
# E_GITLAB_URL="https://gitlab.yourschool.edu"
# E_GITLAB_TOKEN="your_gitlab_token_here"
# E_GITHUB_USERNAME="your_github_username"
# E_GITHUB_TOKEN="your_github_token_here"
#
# IMPORTANT: This script now uses SSH to clone from GitLab.
# You MUST have an SSH key set up on your machine and added to your GitLab account.
# Test this with: ssh -T git@gitlab.yourschool.edu

load_dotenv()

# --- CONFIGURATION FROM .ENV FILE ---

# 1. Your school's GitLab instance URL (e.g., https://gitlab.lnu.se)
GITLAB_URL = os.getenv("E_GITLAB_URL")

# 2. Your GitLab Personal Access Token (with 'api' and 'read_repository' scopes)
GITLAB_PRIVATE_TOKEN = os.getenv("E_GITLAB_TOKEN")

# 3. Your GitHub username
GITHUB_USERNAME = os.getenv("E_GITHUB_USERNAME")

# 4. Your GitHub Personal Access Token (with 'repo' scope)
GITHUB_TOKEN = os.getenv("E_GITHUB_TOKEN")

# 5. Set to True to make new GitHub repos private, False for public.
CREATE_PRIVATE_REPOS = True

# --- END OF CONFIGURATION ---

# New error handler for shutil.rmtree to handle read-only files on Windows
def handle_remove_readonly(func, path, exc_info):
    """
    Error handler for shutil.rmtree.

    If the error is a PermissionError, it changes the file to be writable
    and then re-attempts the removal. Otherwise, it re-raises the error.
    
    Usage: shutil.rmtree(path, onerror=handle_remove_readonly)
    """
    # exc_info contains (type, value, traceback)
    excvalue = exc_info[1]
    if func in (os.rmdir, os.remove, os.unlink) and isinstance(excvalue, PermissionError):
        os.chmod(path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)  # 0777
        func(path)
    else:
        raise

def get_gitlab_projects():
    """Fetches a list of all projects the user is a member of from GitLab."""
    print("Fetching all member projects from GitLab...")
    
    api_url = f"{GITLAB_URL}/api/v4/projects"
    headers = {"PRIVATE-TOKEN": GITLAB_PRIVATE_TOKEN}
    params = {"membership": "true", "per_page": 100} 

    all_projects = []
    page = 1
    while True:
        try:
            params["page"] = page
            response = requests.get(api_url, headers=headers, params=params)
            response.raise_for_status()
            projects = response.json()
            if not projects:
                break 
            all_projects.extend(projects)
            page += 1
        except requests.exceptions.RequestException as e:
            print(f"Error fetching projects from GitLab: {e}")
            return None

    print(f"Successfully found {len(all_projects)} projects.")
    return all_projects


def create_github_repo(project_name, description):
    """Creates a new repository on GitHub, or confirms if it already exists."""
    print(f"Checking for GitHub repository '{project_name}'...")
    
    check_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{project_name}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    
    response = requests.get(check_url, headers=headers)
    
    if response.status_code == 200:
        print(f"Repository '{project_name}' already exists on GitHub. Proceeding to mirror.")
        repo_data = response.json()
        return repo_data["clone_url"]

    elif response.status_code == 404:
        print(f"Repository does not exist. Creating '{project_name}' on GitHub...")
        create_url = "https://api.github.com/user/repos"
        data = { "name": project_name, "description": description, "private": CREATE_PRIVATE_REPOS }
        try:
            response = requests.post(create_url, headers=headers, json=data)
            response.raise_for_status()
            repo_data = response.json()
            print("Successfully created GitHub repository.")
            return repo_data["clone_url"]
        except requests.exceptions.RequestException as e:
            print(f"Error creating GitHub repository '{project_name}': {e}")
            return None
    else:
        print(f"Error checking for repository '{project_name}': {response.status_code} - {response.text}")
        return None


def mirror_repository(gitlab_ssh_url, github_clone_url):
    """Clones a repo from GitLab using SSH and mirrors it to GitHub."""
    repo_name_with_git = gitlab_ssh_url.split('/')[-1]
    local_mirror_path = repo_name_with_git
    
    if os.path.exists(local_mirror_path):
        print(f"Removing existing local directory '{local_mirror_path}'...")
        # Use the error handler to remove potentially read-only files
        shutil.rmtree(local_mirror_path, onerror=handle_remove_readonly)

    print(f"1. Mirroring '{repo_name_with_git}' from GitLab using SSH...")
    try:
        result = subprocess.run(
            ["git", "clone", "--mirror", gitlab_ssh_url, local_mirror_path], 
            check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: Failed to clone from GitLab via SSH.")
        print(f"  Please ensure your SSH key is added to your GitLab account.")
        print(f"  Git Command Output:\n{e.stderr}")
        return False

    original_cwd = os.getcwd()
    os.chdir(local_mirror_path)

    print("2. Pushing mirror to GitHub...")
    try:
        authenticated_github_url = github_clone_url.replace(
            "https://", f"https://{GITHUB_USERNAME}:{GITHUB_TOKEN}@"
        )
        result = subprocess.run(
            ["git", "push", "--mirror", authenticated_github_url], 
            check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: Failed to push to GitHub.")
        print(f"  Git Command Output:\n{e.stderr}")
        os.chdir(original_cwd)
        # Use the error handler here as well for cleanup on failure
        shutil.rmtree(local_mirror_path, onerror=handle_remove_readonly)
        return False
    
    os.chdir(original_cwd)
    # And use the error handler for the final successful cleanup
    shutil.rmtree(local_mirror_path, onerror=handle_remove_readonly)
    print(f"Successfully mirrored '{repo_name_with_git}'.\n")
    return True


def main():
    """Main function to run the migration."""
    print("--- Starting GitLab to GitHub Migration Script ---")
    
    if not all([GITLAB_URL, GITLAB_PRIVATE_TOKEN, GITHUB_USERNAME, GITHUB_TOKEN]):
        print("\nFATAL ERROR: One or more environment variables are missing from your .env file.")
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
        gitlab_ssh_url = project["ssh_url_to_repo"]

        print(f"--- Processing project: {project_name} ---")

        github_clone_url = create_github_repo(project_name, project_description)

        if not github_clone_url:
            print(f"Skipping migration for '{project_name}' due to GitHub repo issue.\n")
            failed_migrations.append(project_name)
            continue
        
        success = mirror_repository(gitlab_ssh_url, github_clone_url)
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