"""
Utility functions for detecting and handling duplicate observations.

This module provides tools to identify true duplicates (observations with identical
patient, attribute, time, location, and value) versus legitimate data variations.
"""

from typing import Any
from django.db.models import Count, Q
from core.models import Observation


def find_duplicate_observations(study=None, raw_data_file=None, limit=50, source_only=True):
    """
    Find observations that are true duplicates (same patient, attribute, time, location, and value).
    
    IMPORTANT: Distinguishes between source data duplicates (problematic) and transformed data 
    duplicates (expected when multiple source files map to same target study).
    
    OPTIMIZED: Only returns summary counts and a limited sample of duplicate groups.
    
    Args:
        study: Optional study to filter by
        raw_data_file: Optional raw data file to filter by
        limit: Maximum number of duplicate groups to return details for (default: 50)
        source_only: If True, only check source observations (not transformed). Default: True.
                     Source duplicates are data quality issues. Transformed duplicates may be 
                     intentional when harmonizing multiple source files to one target study.
        
    Returns:
        dict with:
            - duplicates: List of duplicate groups with details (limited to 'limit')
            - total_duplicates: Total number of duplicate observations
            - duplicate_groups: Number of groups of duplicates
            - is_source: Boolean indicating if these are source (True) or transformed (False) duplicates
    """
    
    # Base query - filter by source vs transformed
    query = Observation.objects.all()
    
    if raw_data_file:
        # For a specific raw data file, check its study's observations
        query = query.filter(attribute__studies=raw_data_file.study)
        
        # Determine if this is source or transformed data based on study purpose
        is_source = raw_data_file.study.study_purpose == 'source'
    elif study:
        query = query.filter(attribute__studies=study)
        is_source = study.study_purpose == 'source'
    else:
        is_source = True  # Default assumption
    
    # OPTIMIZATION: Just count duplicates without fetching all details
    # This is much faster for large datasets
    duplicates_query = (
        query
        .values(
            'patient_id',
            'attribute_id',
            'time_id',
            'location_id',
            'float_value',
            'int_value',
            'text_value',
            'boolean_value',
            'datetime_value',
        )
        .annotate(count=Count('id'))
        .filter(count__gt=1)
        .order_by('-count')
    )
    
    # Get total counts efficiently
    duplicate_groups_count = duplicates_query.count()
    total_duplicate_obs = sum((dup['count'] - 1) for dup in duplicates_query)
    
    # Only fetch details for a limited number of groups
    duplicate_groups = []
    
    for dup in duplicates_query[:limit]:  # LIMIT the detailed queries
        # OPTIMIZATION: Get just the IDs and one sample in a single query
        obs_ids = list(
            query.filter(
                patient_id=dup['patient_id'],
                attribute_id=dup['attribute_id'],
                time_id=dup['time_id'],
                location_id=dup['location_id'],
                float_value=dup['float_value'],
                int_value=dup['int_value'],
                text_value=dup['text_value'],
                boolean_value=dup['boolean_value'],
                datetime_value=dup['datetime_value'],
            )
            .values_list('id', flat=True)[:10]  # Limit IDs too
        )
        
        # Only fetch ONE sample observation with all relations
        sample_obs = query.filter(id=obs_ids[0]).select_related(
            'patient',
            'attribute',
            'time',
            'location',
        ).first()
        
        if sample_obs:
            duplicate_groups.append({
                'observation_ids': obs_ids,
                'count': dup['count'],
                'patient': sample_obs.patient.unique_id if sample_obs.patient else None,
                'attribute': sample_obs.attribute.variable_name,
                'time': sample_obs.time.id if sample_obs.time else None,
                'location': sample_obs.location.id if sample_obs.location else None,
                'value': _get_observation_value(sample_obs),
            })
    
    return {
        'duplicates': duplicate_groups,
        'total_duplicates': total_duplicate_obs,
        'duplicate_groups': duplicate_groups_count,
        'is_source': is_source,
        'data_type': 'source' if is_source else 'transformed',
    }


def find_multi_value_observations(study=None, raw_data_file=None, limit=30):
    """
    Find cases where same patient+attribute+time+location has multiple DIFFERENT values.
    These are not duplicates but data conflicts that need investigation.
    
    OPTIMIZED: Limited to return only a sample of conflict groups.
    
    Args:
        study: Optional study to filter by
        raw_data_file: Optional raw data file to filter by
        limit: Maximum number of conflict groups to return details for (default: 30)
        
    Returns:
        dict with conflict groups and data type indicator
    """
    
    query = Observation.objects.all()
    
    # Determine data type
    if raw_data_file:
        query = query.filter(attribute__studies=raw_data_file.study)
        is_source = raw_data_file.study.study_purpose == 'source'
    elif study:
        query = query.filter(attribute__studies=study)
        is_source = study.study_purpose == 'source'
    else:
        is_source = True
    
    # Find cases where patient+attribute+time+location has multiple observations
    multi_context = (
        query
        .values('patient_id', 'attribute_id', 'time_id', 'location_id')
        .annotate(count=Count('id'))
        .filter(count__gt=1)
        .order_by('-count')  # OPTIMIZATION: Order by count to get worst conflicts first
    )
    
    total_conflict_groups = multi_context.count()
    conflicts = []
    
    # OPTIMIZATION: Only process a limited number of conflict groups
    for item in multi_context[:limit]:
        # Get all observations in this context (but only IDs and values first)
        obs_list = list(
            query.filter(
                patient_id=item['patient_id'],
                attribute_id=item['attribute_id'],
                time_id=item['time_id'],
                location_id=item['location_id'],
            )
            .only('id', 'float_value', 'int_value', 'text_value', 'boolean_value', 'datetime_value')[:10]  # Limit per group
        )
        
        # Get all unique values
        values = set()
        for obs in obs_list:
            val = _get_observation_value(obs)
            if val is not None:
                values.add(str(val))
        
        # Only report if there are actually different values (not just duplicates)
        if len(values) > 1:
            # NOW fetch one full observation with relations for display
            sample_obs = query.filter(
                patient_id=item['patient_id'],
                attribute_id=item['attribute_id'],
                time_id=item['time_id'],
                location_id=item['location_id'],
            ).select_related('patient', 'attribute', 'time', 'location').first()
            
            if sample_obs:
                conflicts.append({
                    'observation_ids': [obs.id for obs in obs_list],
                    'count': len(obs_list),
                    'patient': sample_obs.patient.unique_id if sample_obs.patient else None,
                    'attribute': sample_obs.attribute.variable_name,
                    'time': sample_obs.time.id if sample_obs.time else None,
                    'location': sample_obs.location.id if sample_obs.location else None,
                    'values': list(values),
                })
    
    return {
        'conflicts': conflicts,
        'total_conflicts': len(conflicts),
        'total_conflict_groups': total_conflict_groups,
        'is_source': is_source,
        'data_type': 'source' if is_source else 'transformed',
    }


def delete_duplicate_observations(observation_ids_to_keep):
    """
    Delete duplicate observations, keeping only the specified IDs.
    
    Args:
        observation_ids_to_keep: List of observation IDs to preserve
        
    Returns:
        Number of observations deleted
    """
    # Find all duplicate groups that contain any of the IDs to keep
    duplicates = find_duplicate_observations()
    
    ids_to_delete = []
    for group in duplicates['duplicates']:
        # If any ID in this group should be kept, delete the others
        group_ids = set(group['observation_ids'])
        kept_ids = group_ids.intersection(set(observation_ids_to_keep))
        
        if kept_ids:
            # Delete all except one of the kept IDs
            kept_id = list(kept_ids)[0]
            ids_to_delete.extend([oid for oid in group_ids if oid != kept_id])
    
    # Perform deletion
    if ids_to_delete:
        count = Observation.objects.filter(id__in=ids_to_delete).delete()[0]
        return count
    
    return 0


def _get_observation_value(obs):
    """Helper to extract the value from an observation regardless of type."""
    if obs.float_value is not None:
        return obs.float_value
    elif obs.int_value is not None:
        return obs.int_value
    elif obs.text_value:
        return obs.text_value
    elif obs.boolean_value is not None:
        return obs.boolean_value
    elif obs.datetime_value is not None:
        return obs.datetime_value
    return None
