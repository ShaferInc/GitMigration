import requests
import subprocess
import os
import shutil
import stat
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION FROM .ENV FILE ---
GITLAB_URL = os.getenv("E_GITLAB_URL")
GITLAB_PRIVATE_TOKEN = os.getenv("E_GITLAB_TOKEN")
GITHUB_USERNAME = os.getenv("E_GITHUB_USERNAME")
GITHUB_TOKEN = os.getenv("E_GITHUB_TOKEN")

# --- SCRIPT CONFIGURATION ---
# Set to True to make new GitHub repos private, False for public.
CREATE_PRIVATE_REPOS = True
# Set the max file size in MB. GitHub's limit is 100MB.
# We use 99MB to be safe.
MAX_FILE_SIZE_MB = 99
# Log file to track completed migrations.
COMPLETED_LOG_FILE = "migration_log.txt"

# --- END OF CONFIGURATION ---

def handle_remove_readonly(func, path, exc_info):
    excvalue = exc_info[1]
    if func in (os.rmdir, os.remove, os.unlink) and isinstance(excvalue, PermissionError):
        os.chmod(path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        func(path)
    else:
        raise

def load_completed_migrations():
    """Loads the set of already migrated repo names from the log file."""
    if not os.path.exists(COMPLETED_LOG_FILE):
        return set()
    with open(COMPLETED_LOG_FILE, "r") as f:
        return set(line.strip() for line in f)

def log_completed_migration(project_name):
    """Appends a successfully migrated repo name to the log file."""
    with open(COMPLETED_LOG_FILE, "a") as f:
        f.write(f"{project_name}\n")

def get_gitlab_projects():
    # This function is unchanged.
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
            if not projects: break 
            all_projects.extend(projects)
            page += 1
        except requests.exceptions.RequestException as e:
            print(f"Error fetching projects from GitLab: {e}")
            return None
    print(f"Successfully found {len(all_projects)} projects.")
    return all_projects

def create_github_repo(project_name, description):
    # This function is unchanged.
    print(f"Checking for GitHub repository '{project_name}'...")
    check_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{project_name}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    response = requests.get(check_url, headers=headers)
    if response.status_code == 200:
        print(f"Repository '{project_name}' already exists on GitHub. Proceeding to transfer.")
        return response.json()["clone_url"]
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

def transfer_repository(project_name, gitlab_ssh_url, github_clone_url):
    """
    Clones, filters large files, and pushes a repository to GitHub.
    This is no longer a simple mirror.
    """
    # Clean up previous attempt
    if os.path.exists(project_name):
        print(f"Removing existing local directory '{project_name}'...")
        shutil.rmtree(project_name, onerror=handle_remove_readonly)

    print(f"1. Cloning '{project_name}' from GitLab using SSH...")
    try:
        subprocess.run(
            ["git", "clone", gitlab_ssh_url, project_name], 
            check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: Failed to clone from GitLab via SSH.\n{e.stderr}")
        return False

    original_cwd = os.getcwd()
    os.chdir(project_name)

    print(f"2. Filtering repository to remove files larger than {MAX_FILE_SIZE_MB}MB...")
    try:
        # This command removes blobs (files) bigger than the specified size from history
        subprocess.run(
            ["git", "filter-repo", f"--strip-blobs-bigger-than", f"{MAX_FILE_SIZE_MB}M"],
            check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: Failed to filter repository. Is git-filter-repo installed?\n{e.stderr}")
        os.chdir(original_cwd)
        shutil.rmtree(project_name, onerror=handle_remove_readonly)
        return False

    print("3. Pushing filtered repository to GitHub...")
    try:
        authenticated_github_url = github_clone_url.replace(
            "https://", f"https://{GITHUB_USERNAME}:{GITHUB_TOKEN}@"
        )
        # Set the new remote URL and push all branches and tags
        subprocess.run(["git", "remote", "set-url", "origin", authenticated_github_url], check=True)
        subprocess.run(["git", "push", "origin", "--all"], check=True, capture_output=True, text=True)
        subprocess.run(["git", "push", "origin", "--tags"], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: Failed to push to GitHub.\n{e.stderr}")
        os.chdir(original_cwd)
        shutil.rmtree(project_name, onerror=handle_remove_readonly)
        return False
    
    # Cleanup
    os.chdir(original_cwd)
    shutil.rmtree(project_name, onerror=handle_remove_readonly)
    print(f"Successfully transferred '{project_name}'.\n")
    return True

def main():
    """Main function to run the migration."""
    # Check for git-filter-repo before starting
    if not shutil.which("git-filter-repo"):
        print("\nFATAL ERROR: 'git-filter-repo' is not installed or not in your PATH.")
        print("Please install it by running: python -m pip install git-filter-repo")
        return

    print("--- Starting GitLab to GitHub Migration Script ---")
    
    completed_migrations = load_completed_migrations()
    if completed_migrations:
        print(f"Found {len(completed_migrations)} previously migrated repositories to skip.")

    gitlab_projects = get_gitlab_projects()

    if not gitlab_projects:
        print("No projects found or an error occurred. Exiting.")
        return

    successful_migrations = 0
    failed_migrations = []
    skipped_migrations = 0

    for project in gitlab_projects:
        project_name = project["path"]
        
        # --- LOGIC TO SKIP COMPLETED REPOS ---
        if project_name in completed_migrations:
            skipped_migrations += 1
            continue # Move to the next project

        print(f"--- Processing project: {project_name} ---")
        project_description = project.get("description", "")
        gitlab_ssh_url = project["ssh_url_to_repo"]

        github_clone_url = create_github_repo(project_name, project_description)

        if not github_clone_url:
            print(f"Skipping migration for '{project_name}' due to GitHub repo issue.\n")
            failed_migrations.append(project_name)
            continue
        
        # Use the new transfer function
        success = transfer_repository(project_name, gitlab_ssh_url, github_clone_url)
        if success:
            successful_migrations += 1
            log_completed_migration(project_name) # Log success
        else:
            failed_migrations.append(project_name)

    print("--- Migration Complete ---")
    if skipped_migrations > 0:
        print(f"Skipped: {skipped_migrations} repositories (already migrated).")
    print(f"Successfully migrated: {successful_migrations} new repositories.")
    if failed_migrations:
        print(f"Failed to migrate: {len(failed_migrations)} repositories:")
        for name in failed_migrations:
            print(f"  - {name}")
    print("--------------------------")

if __name__ == "__main__":
    main()