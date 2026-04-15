"""
JIRA API Service Module
Handles authentication, issue fetching, and data extraction from JIRA API
"""
import requests
import os
from typing import Dict, List, Optional, Any
from datetime import datetime


class JiraService:
    """Service for interacting with JIRA API"""

    def __init__(self, jira_url: str, email: str, api_token: str):
        """
        Initialize JIRA service

        Args:
            jira_url: Base URL of JIRA instance (e.g., https://your-domain.atlassian.net)
            email: User email for authentication
            api_token: JIRA API token
        """
        self.base_url = jira_url.rstrip('/')
        self.email = email
        self.api_token = api_token
        self.session = requests.Session()
        self.session.auth = (email, api_token)
        self.session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })

    def test_connection(self) -> Dict[str, Any]:
        """Test connection to JIRA API"""
        try:
            response = self.session.get(f'{self.base_url}/rest/api/3/myself')
            if response.status_code == 200:
                user = response.json()
                return {
                    'success': True,
                    'message': f'Connected as {user.get("displayName", "Unknown")}',
                    'user': user
                }
            return {
                'success': False,
                'message': f'Connection failed: {response.status_code}',
                'error': response.text
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'Connection error: {str(e)}'
            }

    def get_project_issues(self, project_key: str, max_results: int = 1000) -> Dict[str, Any]:
        """
        Fetch all issues from a JIRA project using JIRA API v3 /search/jql endpoint

        Args:
            project_key: JIRA project key (e.g., 'PROJ')
            max_results: Maximum number of issues to fetch

        Returns:
            Dictionary containing issues and metadata
        """
        jql = f'project = "{project_key}" ORDER BY created DESC'

        try:
            issues = []
            batch_size = min(100, max_results)
            next_page_token = None

            while len(issues) < max_results:
                payload = {
                    'jql': jql,
                    'fields': ['key', 'summary', 'issuetype', 'priority', 'status', 'created', 'updated', 'labels', 'components'],
                    'maxResults': batch_size
                }
                if next_page_token:
                    payload['nextPageToken'] = next_page_token

                response = self.session.post(
                    f'{self.base_url}/rest/api/3/search/jql',
                    json=payload
                )

                if response.status_code != 200:
                    return {
                        'success': False,
                        'message': f'API error: {response.status_code}',
                        'error': response.text
                    }

                data = response.json()
                batch_issues = data.get('issues', [])

                if not batch_issues:
                    break

                issues.extend(batch_issues)
                next_page_token = data.get('nextPageToken')

                if not next_page_token or len(issues) >= max_results:
                    break

            return {
                'success': True,
                'issues': issues,
                'total': len(issues),
                'project_key': project_key
            }

        except Exception as e:
            return {
                'success': False,
                'message': f'Error fetching issues: {str(e)}'
            }

    def get_bugs_only(self, project_key: str, max_results: int = 500) -> Dict[str, Any]:
        """
        Fetch only bug issues from a JIRA project using JIRA API v3 /search/jql endpoint

        Args:
            project_key: JIRA project key
            max_results: Maximum number of issues to fetch

        Returns:
            Dictionary containing bug issues
        """
        jql = f'project = "{project_key}" AND issuetype = "Bug" ORDER BY priority DESC, created DESC'

        try:
            issues = []
            batch_size = min(100, max_results)
            next_page_token = None

            while len(issues) < max_results:
                payload = {
                    'jql': jql,
                    'fields': ['key', 'summary', 'issuetype', 'priority', 'status', 'created', 'updated', 'labels', 'components'],
                    'maxResults': batch_size
                }
                if next_page_token:
                    payload['nextPageToken'] = next_page_token

                response = self.session.post(
                    f'{self.base_url}/rest/api/3/search/jql',
                    json=payload
                )

                if response.status_code != 200:
                    return {
                        'success': False,
                        'message': f'API error: {response.status_code}',
                        'error': response.text
                    }

                data = response.json()
                batch_issues = data.get('issues', [])

                if not batch_issues:
                    break

                issues.extend(batch_issues)
                next_page_token = data.get('nextPageToken')

                if not next_page_token or len(issues) >= max_results:
                    break

            return {
                'success': True,
                'issues': issues,
                'total': len(issues),
                'project_key': project_key
            }

        except Exception as e:
            return {
                'success': False,
                'message': f'Error fetching bugs: {str(e)}'
            }

    def get_issue_types(self, project_key: str) -> Dict[str, Any]:
        """Get all issue types available in the project"""
        try:
            response = self.session.get(
                f'{self.base_url}/rest/api/3/project/{project_key}'
            )
            if response.status_code == 200:
                project = response.json()
                issue_types = project.get('issueTypes', [])
                return {
                    'success': True,
                    'issue_types': issue_types
                }
            return {
                'success': False,
                'message': f'Error: {response.status_code}'
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'Error: {str(e)}'
            }

    def get_projects(self) -> Dict[str, Any]:
        """Get all accessible projects for the authenticated user"""
        try:
            response = self.session.get(
                f'{self.base_url}/rest/api/3/project'
            )
            if response.status_code == 200:
                projects = response.json()
                return {
                    'success': True,
                    'projects': projects
                }
            return {
                'success': False,
                'message': f'Error: {response.status_code}'
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'Error: {str(e)}'
            }


def extract_issue_data(issues: List[Dict]) -> List[Dict]:
    """
    Extract relevant data from JIRA issues for ML processing

    Args:
        issues: List of JIRA issue objects

    Returns:
        List of extracted issue data
    """
    extracted = []

    for issue in issues:
        fields = issue.get('fields', {})

        # Extract priority value
        priority = fields.get('priority', {})
        priority_name = priority.get('name', 'Unknown') if priority else 'Unknown'
        priority_id = priority.get('id', '0') if priority else '0'

        # Extract status
        status = fields.get('status', {})
        status_name = status.get('name', 'Unknown') if status else 'Unknown'
        status_category = status.get('statusCategory', {}).get('key', 'unknown') if status else 'unknown'

        # Extract issue type
        issue_type = fields.get('issuetype', {})
        issue_type_name = issue_type.get('name', 'Unknown') if issue_type else 'Unknown'

        # Parse dates
        created = fields.get('created', '')
        updated = fields.get('updated', '')

        # Calculate priority weight
        priority_weight = calculate_priority_weight(priority_name)

        extracted.append({
            'key': issue.get('key', ''),
            'summary': fields.get('summary', ''),
            'issue_type': issue_type_name,
            'priority': priority_name,
            'priority_id': int(priority_id) if priority_id.isdigit() else 0,
            'priority_weight': priority_weight,
            'status': status_name,
            'status_category': status_category,
            'created': created,
            'updated': updated,
            'labels': fields.get('labels', []),
            'is_bug': issue_type_name.lower() == 'bug',
            'is_open': status_category not in ['done', 'closed']
        })

    return extracted


def calculate_priority_weight(priority_name: str) -> float:
    """Convert priority name to numeric weight"""
    priority_map = {
        'highest': 5.0,
        'high': 4.0,
        'medium': 3.0,
        'low': 2.0,
        'lowest': 1.0
    }
    return priority_map.get(priority_name.lower(), 2.5)


def calculate_status_weight(status_category: str) -> float:
    """Convert status category to numeric weight"""
    status_map = {
        'new': 1.0,
        'indeterminate': 2.0,
        'done': 3.0
    }
    return status_map.get(status_category, 1.5)
