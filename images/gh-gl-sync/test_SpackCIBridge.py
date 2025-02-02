import os
from unittest.mock import create_autospec, patch, Mock

import SpackCIBridge


class AttrDict(dict):
    def __init__(self, iterable, **kwargs):
        super(AttrDict, self).__init__(iterable, **kwargs)
        for key, value in iterable.items():
            if isinstance(value, dict):
                self.__dict__[key] = AttrDict(value)
            else:
                self.__dict__[key] = value


def test_list_github_prs(capfd):
    """Test the list_github_prs method."""
    github_pr_response = [
        AttrDict({
            "number": 1,
            "merge_commit_sha": "aaaaaaaa",
            "head": {
                "ref": "improve_docs",
                "sha": "shafoo"
            },
            "base": {
                "sha": "shabar"
            }
        }),
        AttrDict({
            "number": 2,
            "merge_commit_sha": "bbbbbbbb",
            "head": {
                "ref": "fix_test",
                "sha": "shagah"
            },
            "base": {
                "sha": "shafaz"
            }
        }),
    ]
    gh_repo = Mock()
    gh_repo.get_pulls.return_value = github_pr_response
    bridge = SpackCIBridge.SpackCIBridge()
    bridge.py_gh_repo = gh_repo

    import subprocess
    actual_run_method = subprocess.run
    mock_run_return = Mock()
    mock_run_return.stdout = b"Merge shagah into ccccccc"
    subprocess.run = create_autospec(subprocess.run, return_value=mock_run_return)
    retval = bridge.list_github_prs()
    subprocess.run = actual_run_method

    github_prs = retval[0]
    assert github_prs["pr_strings"] == ["pr1_improve_docs", "pr2_fix_test"]
    assert github_prs["merge_commit_shas"] == ["aaaaaaaa", "bbbbbbbb"]
    assert gh_repo.get_pulls.call_count == 1
    out, err = capfd.readouterr()
    expected = """Skip pushing pr2_fix_test because GitLab already has HEAD shagah
All Open PRs:
    pr1_improve_docs
    pr2_fix_test
Filtered Open PRs:
    pr1_improve_docs
"""
    assert out == expected


def test_list_github_protected_branches(capfd):
    """Test the list_github_protected_branches method and verify that we do not
       push main_branch commits when it already has a pipeline running."""
    github_branches_response = [
        AttrDict({
            "name": "alpha",
            "protected": True
        }),
        AttrDict({
          "name": "develop",
          "protected": True
        }),
        AttrDict({
          "name": "feature",
          "protected": False
        }),
        AttrDict({
          "name": "main",
          "protected": True
        }),
        AttrDict({
          "name": "release",
          "protected": True
        }),
        AttrDict({
          "name": "wip",
          "protected": False
        }),
    ]
    gh_repo = Mock()
    gh_repo.get_branches.return_value = github_branches_response
    bridge = SpackCIBridge.SpackCIBridge(main_branch="develop")
    bridge.currently_running_sha = "aaaaaaaa"
    bridge.py_gh_repo = gh_repo
    protected_branches = bridge.list_github_protected_branches()
    assert protected_branches == ["alpha", "main", "release"]
    expected = "Skip pushing develop because it already has a pipeline running (aaaaaaaa)"
    out, err = capfd.readouterr()
    assert expected in out


def test_get_synced_prs(capfd):
    """Test the get_synced_prs method."""
    bridge = SpackCIBridge.SpackCIBridge()
    bridge.get_gitlab_pr_branches = lambda *args: None
    bridge.gitlab_pr_output = b"""
  gitlab/github/pr1_example
  gitlab/github/pr2_another_try
    """
    assert bridge.get_synced_prs() == ["pr1_example", "pr2_another_try"]
    out, err = capfd.readouterr()
    assert out == "Synced PRs:\n    pr1_example\n    pr2_another_try\n"


def test_get_prs_to_delete(capfd):
    """Test the get_prs_to_delete method."""
    open_prs = ["pr3_try_this", "pr4_new_stuff"]
    synced_prs = ["pr1_first_try", "pr2_different_approach", "pr3_try_this"]
    bridge = SpackCIBridge.SpackCIBridge()
    closed_refspecs = bridge.get_prs_to_delete(open_prs, synced_prs)
    assert closed_refspecs == [":github/pr1_first_try", ":github/pr2_different_approach"]
    out, err = capfd.readouterr()
    assert out == "Synced Closed PRs:\n    pr1_first_try\n    pr2_different_approach\n"


def test_get_open_refspecs():
    """Test the get_open_refspecs and update_refspecs_for_protected_branches methods."""
    open_prs = {
        "pr_strings": ["pr1_this", "pr2_that"],
        "merge_commit_shas": ["aaaaaaaa", "bbbbbbbb"],
        "base_shas": ["shafoo", "shabar"],
        "head_shas": ["shabaz", "shagah"],
        "backlogged": [False, False]
    }
    bridge = SpackCIBridge.SpackCIBridge()
    open_refspecs, fetch_refspecs = bridge.get_open_refspecs(open_prs)
    assert open_refspecs == [
        "github/pr1_this:github/pr1_this",
        "github/pr2_that:github/pr2_that"
    ]
    assert fetch_refspecs == [
        "+aaaaaaaa:refs/remotes/github/pr1_this",
        "+bbbbbbbb:refs/remotes/github/pr2_that"
    ]

    protected_branches = ["develop", "master"]
    bridge.update_refspecs_for_protected_branches(protected_branches, open_refspecs, fetch_refspecs)
    assert open_refspecs == [
        "github/pr1_this:github/pr1_this",
        "github/pr2_that:github/pr2_that",
        "github/develop:github/develop",
        "github/master:github/master",
    ]
    assert fetch_refspecs == [
        "+aaaaaaaa:refs/remotes/github/pr1_this",
        "+bbbbbbbb:refs/remotes/github/pr2_that",
        "+refs/heads/develop:refs/remotes/github/develop",
        "+refs/heads/master:refs/remotes/github/master"
    ]


def test_ssh_agent():
    """Test starting & stopping ssh-agent."""
    def check_pid(pid):
        """Local function to check if a PID is running or not."""
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        else:
            return True

    # Read in our private key.
    # Don't worry, this key was just generated for testing.
    # It's not actually used for anything.
    key_file = open("test_key.base64", "r")
    ssh_key_base64 = key_file.read()
    key_file.close()

    # Start ssh-agent.
    bridge = SpackCIBridge.SpackCIBridge()
    bridge.setup_ssh(ssh_key_base64)

    assert "SSH_AGENT_PID" in os.environ
    pid = int(os.environ["SSH_AGENT_PID"])
    assert check_pid(pid)

    # Run our cleanup function to kill the ssh-agent.
    SpackCIBridge.SpackCIBridge.cleanup()

    # Make sure it's not running any more.
    # The loop/sleep is to give the process a little time to shut down.
    import time
    for i in range(10):
        if check_pid(pid):
            time.sleep(0.01)
    assert not check_pid(pid)

    # Prevent atexit from trying to kill it again.
    del os.environ["SSH_AGENT_PID"]


def test_get_pipeline_api_template():
    """Test that pipeline_api_template get constructed properly."""
    bridge = SpackCIBridge.SpackCIBridge(gitlab_host="https://gitlab.spack.io", gitlab_project="zack/my_test_proj")
    template = bridge.pipeline_api_template
    assert template[0:84] == "https://gitlab.spack.io/api/v4/projects/zack%2Fmy_test_proj/pipelines?updated_after="
    assert template.endswith("&ref={1}")


def test_dedupe_pipelines():
    """Test the dedupe_pipelines method."""
    input = [
        {
            "id": 1,
            "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "ref": "github/pr1_readme",
            "status": "failed",
            "created_at": "2020-08-26T17:26:30.216Z",
            "updated_at": "2020-08-26T17:26:36.807Z",
            "web_url": "https://gitlab.spack.io/zack/my_test_proj/pipelines/1"
        },
        {
            "id": 2,
            "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "ref": "github/pr1_readme",
            "status": "passed",
            "created_at": "2020-08-27T17:27:30.216Z",
            "updated_at": "2020-08-27T17:27:36.807Z",
            "web_url": "https://gitlab.spack.io/zack/my_test_proj/pipelines/2"
        },
        {
            "id": 3,
            "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "ref": "github/pr2_todo",
            "status": "failed",
            "created_at": "2020-08-26T17:26:30.216Z",
            "updated_at": "2020-08-26T17:26:36.807Z",
            "web_url": "https://gitlab.spack.io/zack/my_test_proj/pipelines/3"
        },
    ]
    expected = {
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": {
            "id": 2,
            "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "ref": "github/pr1_readme",
            "status": "passed",
            "created_at": "2020-08-27T17:27:30.216Z",
            "updated_at": "2020-08-27T17:27:36.807Z",
            "web_url": "https://gitlab.spack.io/zack/my_test_proj/pipelines/2"
        },
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb": {
            "id": 3,
            "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "ref": "github/pr2_todo",
            "status": "failed",
            "created_at": "2020-08-26T17:26:30.216Z",
            "updated_at": "2020-08-26T17:26:36.807Z",
            "web_url": "https://gitlab.spack.io/zack/my_test_proj/pipelines/3"
        },
    }
    bridge = SpackCIBridge.SpackCIBridge()
    assert bridge.dedupe_pipelines(input) == expected


def test_make_status_for_pipeline():
    """Test the make_status_for_pipeline method."""
    bridge = SpackCIBridge.SpackCIBridge()
    pipeline = {"web_url": "foo"}
    status = bridge.make_status_for_pipeline(pipeline)
    assert status == {}

    pipeline["status"] = "canceled"
    status = bridge.make_status_for_pipeline(pipeline)
    assert status == {}

    test_cases = [
        {
            "input": "created",
            "state": "pending",
            "description": "Pipeline has been created",
        },
        {
            "input": "waiting_for_resource",
            "state": "pending",
            "description": "Pipeline is waiting for resources",
        },
        {
            "input": "preparing",
            "state": "pending",
            "description": "Pipeline is preparing",
        },
        {
            "input": "pending",
            "state": "pending",
            "description": "Pipeline is pending",
        },
        {
            "input": "running",
            "state": "pending",
            "description": "Pipeline is running",
        },
        {
            "input": "manual",
            "state": "pending",
            "description": "Pipeline is running manually",
        },
        {
            "input": "scheduled",
            "state": "pending",
            "description": "Pipeline is scheduled",
        },
        {
            "input": "failed",
            "state": "error",
            "description": "Pipeline failed",
        },
        {
            "input": "skipped",
            "state": "failure",
            "description": "Pipeline was skipped",
        },
        {
            "input": "success",
            "state": "success",
            "description": "Pipeline succeeded",
        },
    ]
    for test_case in test_cases:
        pipeline["status"] = test_case["input"]
        status = bridge.make_status_for_pipeline(pipeline)
        assert status["state"] == test_case["state"]
        assert status["description"] == test_case["description"]


class FakeResponse:
    status: int
    data: bytes

    def __init__(self, *, data: bytes):
        self.data = data

    def read(self):
        self.status = 201 if self.data is not None else 404
        return self.data

    def close(self):
        pass


def test_post_pipeline_status(capfd):
    """Test the post_pipeline_status method."""
    open_prs = {
        "pr_strings": ["pr1_readme"],
        "merge_commit_shas": ["aaaaaaaa"],
        "base_shas": ["shafoo"],
        "head_shas": ["shabaz"],
        "backlogged": [False]
    }

    gh_commit = Mock()
    gh_commit.create_status.return_value = AttrDict({"state": "error"})
    gh_repo = Mock()
    gh_repo.get_commit.return_value = gh_commit

    bridge = SpackCIBridge.SpackCIBridge(gitlab_host="https://gitlab.spack.io",
                                         gitlab_project="zack/my_test_proj",
                                         github_project="zack/my_test_proj")
    bridge.py_gh_repo = gh_repo
    os.environ["GITHUB_TOKEN"] = "my_github_token"

    mock_data = b'''[
        {
            "id": 1,
            "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "ref": "github/pr1_readme",
            "status": "failed",
            "created_at": "2020-08-26T17:26:30.216Z",
            "updated_at": "2020-08-26T17:26:36.807Z",
            "web_url": "https://gitlab.spack.io/zack/my_test_proj/pipelines/1"
        }
    ]'''
    with patch('urllib.request.urlopen', return_value=FakeResponse(data=mock_data)) as mock_urlopen:
        bridge.post_pipeline_status(open_prs, [])
        assert mock_urlopen.call_count == 2
        assert gh_repo.get_commit.call_count == 1
        assert gh_commit.create_status.call_count == 1
    out, err = capfd.readouterr()
    expected_content = "  pr1_readme -> aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
    assert expected_content in out
    del os.environ["GITHUB_TOKEN"]


def test_pipeline_status_backlogged_by_main_branch(capfd):
    """Test the post_pipeline_status method for a PR that is backlogged because its base is being tested."""
    open_prs = {
        "pr_strings": ["pr1_readme"],
        "merge_commit_shas": ["aaaaaaaa"],
        "base_shas": ["shafoo"],
        "head_shas": ["shabaz"],
        "backlogged": ["base"]
    }

    gh_commit = Mock()
    gh_commit.create_status.return_value = AttrDict({"state": "pending"})
    gh_repo = Mock()
    gh_repo.get_commit.return_value = gh_commit

    bridge = SpackCIBridge.SpackCIBridge(gitlab_host="https://gitlab.spack.io",
                                         gitlab_project="zack/my_test_proj",
                                         github_project="zack/my_test_proj",
                                         main_branch="develop")
    bridge.py_gh_repo = gh_repo
    os.environ["GITHUB_TOKEN"] = "my_github_token"

    currently_running_url = "https://gitlab.spack.io/zack/my_test_proj/pipelines/4"
    bridge.currently_running_url = currently_running_url
    expected_desc = "waiting for base develop commit pipeline to succeed"

    bridge.post_pipeline_status(open_prs, [])
    assert gh_commit.create_status.call_count == 1
    gh_commit.create_status.assert_called_with(
        state="pending",
        context="ci/gitlab-ci",
        description=expected_desc,
        target_url=(currently_running_url,)
    )
    out, err = capfd.readouterr()
    expected_content = """Posting backlogged status to the following:
  pr1_readme -> shabaz"""
    assert expected_content in out
    del os.environ["GITHUB_TOKEN"]


def test_pipeline_status_backlogged_by_checks(capfd):
    """Test the post_pipeline_status method for a PR that is backlogged because of a required check."""

    """Helper function to parameterize the test"""
    def verify_backlogged_by_checks(capfd, checks_return_value):
        github_pr_response = [
            AttrDict({
                "number": 1,
                "merge_commit_sha": "aaaaaaaa",
                "head": {
                    "ref": "improve_docs",
                    "sha": "shafoo"
                },
                "base": {
                    "sha": "shabar"
                }
            }),
        ]

        gh_commit = Mock()
        gh_commit.get_check_runs.return_value = checks_return_value
        gh_commit.create_status.return_value = AttrDict({"state": "pending"})

        gh_repo = Mock()
        gh_repo.get_pulls.return_value = github_pr_response
        gh_repo.get_commit.return_value = gh_commit

        bridge = SpackCIBridge.SpackCIBridge(gitlab_host="https://gitlab.spack.io",
                                             gitlab_project="zack/my_test_proj",
                                             github_project="zack/my_test_proj",
                                             prereq_checks=["style"])
        bridge.py_gh_repo = gh_repo
        bridge.currently_running_sha = None

        import subprocess
        actual_run_method = subprocess.run
        mock_run_return = Mock()
        mock_run_return.stdout = b"Merge shagah into ccccccc"

        subprocess.run = create_autospec(subprocess.run, return_value=mock_run_return)
        all_open_prs, open_prs = bridge.list_github_prs()
        subprocess.run = actual_run_method

        os.environ["GITHUB_TOKEN"] = "my_github_token"

        expected_desc = open_prs["backlogged"][0]
        assert expected_desc == "waiting for style check to succeed"

        bridge.post_pipeline_status(open_prs, [])
        assert gh_commit.create_status.call_count == 1
        gh_commit.create_status.assert_called_with(
            state="pending",
            context="ci/gitlab-ci",
            description=expected_desc,
            target_url="",
        )
        out, err = capfd.readouterr()
        expected_content = """Posting backlogged status to the following:
  pr1_improve_docs -> shafoo"""
        assert expected_content in out

        del os.environ["GITHUB_TOKEN"]

    # Verify backlogged status when the required check hasn't passed successfully.
    checks_return_value = [
        AttrDict({
            "name": "style",
            "status": "in_progress",
            "conclusion": None,
        })
    ]
    verify_backlogged_by_checks(capfd, checks_return_value)

    # Verify backlogged status when the required check is missing from the API's response.
    checks_api_response = []
    verify_backlogged_by_checks(capfd, checks_api_response)
