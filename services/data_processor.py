"""
Data Processing Layer for JIRA Issues
Transforms JIRA issues into ML-ready dataset with metrics and risk indicators
"""
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional
from datetime import datetime
from collections import defaultdict

from .jira_service import calculate_priority_weight, calculate_status_weight


def issues_to_dataframe(issues: List[Dict]) -> pd.DataFrame:
    """
    Convert list of extracted JIRA issues to pandas DataFrame

    Args:
        issues: List of extracted issue dictionaries

    Returns:
        pandas DataFrame with structured issue data
    """
    df = pd.DataFrame(issues)

    # Add numeric columns for ML
    df['priority_weight'] = df['priority'].apply(calculate_priority_weight)
    df['status_weight'] = df['status_category'].apply(calculate_status_weight)

    # Bug indicator
    df['is_bug_numeric'] = df['is_bug'].astype(int)
    df['is_open_numeric'] = df['is_open'].astype(int)

    return df


def group_by_component(
    issues: List[Dict],
    components_field: str = 'labels'
) -> Dict[str, Dict]:
    """
    Group issues by component/label and calculate aggregate metrics

    Args:
        issues: List of extracted issue dictionaries
        components_field: Field name to group by ('labels' or 'components')

    Returns:
        Dictionary with component names as keys and metrics as values
    """
    component_metrics = defaultdict(lambda: {
        'total_issues': 0,
        'bugs': 0,
        'high_priority_bugs': 0,
        'open_issues': 0,
        'closed_issues': 0,
        'priority_sum': 0.0,
        'issues': []
    })

    high_priority_threshold = 3.5  # Above medium

    for issue in issues:
        # Get components/labels
        components = issue.get('labels', [])
        if not components:
            components = ['_uncategorized_']

        for comp in components:
            metrics = component_metrics[comp]
            metrics['total_issues'] += 1
            metrics['issues'].append(issue['key'])

            if issue['is_bug']:
                metrics['bugs'] += 1
                if issue['priority_weight'] >= high_priority_threshold:
                    metrics['high_priority_bugs'] += 1

            if issue['is_open']:
                metrics['open_issues'] += 1
            else:
                metrics['closed_issues'] += 1

            metrics['priority_sum'] += issue['priority_weight']

    # Calculate averages and rates
    result = {}
    for comp, metrics in component_metrics.items():
        total = metrics['total_issues']
        result[comp] = {
            'total_issues': total,
            'bug_count': metrics['bugs'],
            'high_priority_bugs': metrics['high_priority_bugs'],
            'open_issues': metrics['open_issues'],
            'closed_issues': metrics['closed_issues'],
            'avg_priority': metrics['priority_sum'] / total if total > 0 else 0,
            'bug_rate': metrics['bugs'] / total if total > 0 else 0,
            'open_rate': metrics['open_issues'] / total if total > 0 else 0,
            'issue_keys': metrics['issues']
        }

    return result


def create_ml_dataset(
    component_metrics: Dict[str, Dict],
    add_synthetic_metrics: bool = True,
    loc_range: tuple = (500, 5000),
    complexity_range: tuple = (1, 20)
) -> pd.DataFrame:
    """
    Create ML-ready dataset from component metrics

    Args:
        component_metrics: Output from group_by_component()
        add_synthetic_metrics: Whether to add synthetic LOC/complexity metrics
        loc_range: Range for synthetic LOC values (min, max)
        complexity_range: Range for synthetic complexity values

    Returns:
        DataFrame ready for ML model input
    """
    rows = []
    np.random.seed(42)  # For reproducibility

    for component, metrics in component_metrics.items():
        row = {
            'module_name': component,
            'total_issues': metrics['total_issues'],
            'defect_count': metrics['bug_count'],
            'high_priority_defects': metrics['high_priority_bugs'],
            'avg_priority': round(metrics['avg_priority'], 2),
            'bug_rate': round(metrics['bug_rate'], 3),
            'open_rate': round(metrics['open_rate'], 3),
            'open_issues': metrics['open_issues'],
            'closed_issues': metrics['closed_issues']
        }

        # Add synthetic metrics if JIRA doesn't provide them
        if add_synthetic_metrics:
            # Generate LOC correlated with issue count (more code = more issues)
            base_loc = loc_range[0] + (metrics['total_issues'] / max(1, len(component_metrics))) * (loc_range[1] - loc_range[0])
            row['loc'] = int(base_loc + np.random.uniform(-500, 500))
            row['loc'] = max(loc_range[0], min(loc_range[1], row['loc']))

            # Generate complexity correlated with bug rate
            base_complexity = complexity_range[0] + metrics['bug_rate'] * 10
            row['complexity'] = round(base_complexity + np.random.uniform(-2, 2), 1)
            row['complexity'] = max(complexity_range[0], min(complexity_range[1], row['complexity']))

            # Churn (changes) correlated with open issues
            row['churn'] = int(metrics['open_issues'] * 2 + np.random.randint(0, 10))

        rows.append(row)

    df = pd.DataFrame(rows)

    # Add defect density metric
    df['defect_density'] = df['defect_count'] / df['loc'] * 1000 if 'loc' in df.columns else df['defect_count'] / df['total_issues']

    return df


def prepare_features_for_ml(df: pd.DataFrame) -> tuple:
    """
    Prepare feature matrix and module names for ML model

    Args:
        df: DataFrame from create_ml_dataset()

    Returns:
        Tuple of (feature_columns, module_names, feature_matrix)
    """
    feature_columns = [
        'total_issues',
        'defect_count',
        'high_priority_defects',
        'avg_priority',
        'bug_rate',
        'open_rate',
        'defect_density'
    ]

    # Add synthetic metrics if present
    for col in ['loc', 'complexity', 'churn']:
        if col in df.columns:
            feature_columns.append(col)

    module_names = df['module_name'].tolist()
    X = df[feature_columns].fillna(0).values

    return feature_columns, module_names, X


def add_risk_predictions(
    df: pd.DataFrame,
    risk_levels: List[str],
    risk_scores: List[int]
) -> pd.DataFrame:
    """
    Add ML model predictions to the dataset

    Args:
        df: DataFrame with module metrics
        risk_levels: List of risk levels from ML model
        risk_scores: List of risk scores from ML model

    Returns:
        DataFrame with added risk columns
    """
    df = df.copy()
    df['risk_level'] = risk_levels
    df['risk_score'] = risk_scores

    return df


def generate_summary_statistics(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Generate summary statistics for dashboard display

    Args:
        df: DataFrame with risk predictions

    Returns:
        Dictionary with summary statistics
    """
    total = len(df)
    high = int((df['risk_level'] == 'HIGH').sum())
    medium = int((df['risk_level'] == 'MEDIUM').sum())
    low = int((df['risk_level'] == 'LOW').sum())

    total_defects = int(df['defect_count'].sum())
    high_priority_defects = int(df['high_priority_defects'].sum())

    return {
        'total_modules': total,
        'high_risk': high,
        'medium_risk': medium,
        'low_risk': low,
        'success_rate': round((low / total) * 100, 2) if total > 0 else 0,
        'total_defects': total_defects,
        'high_priority_defects': high_priority_defects,
        'avg_defects_per_module': round(total_defects / total, 2) if total > 0 else 0,
        'defect_density_avg': round(df['defect_density'].mean(), 4) if 'defect_density' in df.columns else 0
    }
