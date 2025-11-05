# HarmonAIze Architecture Corrections

## Problem Statement

The current architecture has Climate module components directly connecting to the Users module, bypassing the Core module. This violates the intended Core-centric design pattern where all module interactions should flow through the Core module.

## Current Architecture Issues

### Wrong Direct User Connections

1. **climate/models.py:56**
   ```python
   class ClimateDataSource(models.Model):
       created_by = models.ForeignKey(User, on_delete=models.SET_NULL, ...)
   ```
   **Problem:** Direct relationship to User bypassing Core

2. **climate/models.py:267**
   ```python
   class ClimateDataRequest(models.Model):
       requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, ...)
   ```
   **Problem:** Direct relationship to User bypassing Core

### Correct Connections (Keep These)

1. **climate/models.py:207-212**
   ```python
   study = models.ForeignKey(Study, on_delete=models.CASCADE, ...)
   ```
   **Good:** Climate connects to Study in Core

2. **climate/models.py:227-230**
   ```python
   locations = models.ManyToManyField(Location, ...)
   ```
   **Good:** Climate connects to Location in Core

## Required Changes

### Option 1: Remove Direct User References (Recommended)

**Rationale:** Authorization and ownership should be determined through the Study relationship.

#### Changes to `climate/models.py`:

1. **Remove `created_by` from `ClimateDataSource`:**
   ```python
   class ClimateDataSource(models.Model):
       # REMOVE THIS:
       # created_by = models.ForeignKey(User, on_delete=models.SET_NULL, ...)

       # Authorization determined by which Studies use this source
       # Users who created those Studies have access
   ```

2. **Remove `requested_by` from `ClimateDataRequest`:**
   ```python
   class ClimateDataRequest(models.Model):
       study = models.ForeignKey(Study, ...)  # KEEP THIS

       # REMOVE THIS:
       # requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, ...)

       # Instead, get user via: self.study.created_by
   ```

3. **Add property methods for authorization:**
   ```python
   class ClimateDataRequest(models.Model):
       # ... existing fields ...

       @property
       def requested_by(self):
           """Get the user who owns the study (and thus this request)."""
           return self.study.created_by

       def user_can_access(self, user):
           """Check if a user can access this request."""
           return self.study.created_by == user
   ```

### Option 2: Track User but Access Through Core (Alternative)

If you need to track who specifically made a request (different from Study owner):

```python
class ClimateDataRequest(models.Model):
    study = models.ForeignKey(Study, ...)  # Primary relationship

    # Keep for tracking but NOT for primary authorization
    _requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        db_column='requested_by_id',
        help_text="User who created this request (for audit trail only)"
    )

    @property
    def requested_by(self):
        """Always return the study owner for authorization."""
        return self.study.created_by

    @property
    def actual_requester(self):
        """Return who actually clicked the button (if different)."""
        return self._requested_by or self.study.created_by
```

## View Changes Required

Update views to use the Study relationship for authorization:

### climate/views.py

**Before:**
```python
def climate_request_list(request):
    requests = ClimateDataRequest.objects.filter(requested_by=request.user)
```

**After:**
```python
def climate_request_list(request):
    # Get requests through Studies owned by user
    requests = ClimateDataRequest.objects.filter(study__created_by=request.user)
```

**Before:**
```python
def create_climate_request(request, study_id):
    climate_request = ClimateDataRequest.objects.create(
        study=study,
        requested_by=request.user,  # WRONG
        ...
    )
```

**After:**
```python
def create_climate_request(request, study_id):
    study = get_object_or_404(Study, pk=study_id, created_by=request.user)
    climate_request = ClimateDataRequest.objects.create(
        study=study,
        # No requested_by needed - use study.created_by
        ...
    )
```

## Migration Path

1. **Create migration to add property methods** (no schema change needed if using Option 1)
2. **Update all views to use `study__created_by` instead of `requested_by`**
3. **Update templates that reference `request.requested_by`**
4. **After testing, create migration to remove the User foreign keys**

### Migration Commands

```bash
# 1. First, update the model code (remove ForeignKey, add @property)
# 2. Create migration
python manage.py makemigrations climate --name remove_direct_user_relationships

# 3. Review the migration
python manage.py sqlmigrate climate <migration_number>

# 4. Run migration
python manage.py migrate

# 5. Test thoroughly
python manage.py test climate
```

## Benefits of Core-Centric Architecture

1. **Single Source of Truth:** All authorization flows through Study ownership
2. **Simpler Permission Logic:** Check `study.created_by` instead of multiple user fields
3. **Better Modularity:** Climate module doesn't need to know about User model
4. **Easier to Extend:** Adding new modules follows same pattern through Core
5. **Cleaner Data Model:** Removes redundant user tracking across modules

## Testing Checklist

After making changes, verify:

- [ ] Users can only see their own climate requests
- [ ] Users can only create requests for their own studies
- [ ] Climate request list view filters correctly
- [ ] Climate request detail view checks permissions
- [ ] Admin interface still works
- [ ] Existing data migrates correctly
- [ ] No broken templates referencing `requested_by` directly

## File Changes Summary

Files that need modification:

1. `climate/models.py` - Remove User ForeignKeys, add property methods
2. `climate/views.py` - Update filters to use `study__created_by`
3. `climate/templates/climate/*.html` - Update any references to `requested_by`
4. Migration files - Auto-generated by makemigrations
5. `climate/tests.py` - Update tests to reflect new authorization pattern

## Diagram

See `docs/architecture_corrected.svg` for visual representation of current vs. intended architecture.
