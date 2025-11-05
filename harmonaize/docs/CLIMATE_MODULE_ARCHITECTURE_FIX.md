# Climate Module Architecture Fix - Core-Centric Design

## Summary

Successfully refactored the Climate module to follow the Core-centric architecture pattern where all module interactions flow through the Core module, eliminating direct User relationships from the Climate module.

## Changes Made

### 1. Model Updates (climate/models.py)

#### Removed Direct User Imports
```python
# BEFORE
from django.contrib.auth import get_user_model
User = get_user_model()

# AFTER
# No User import - authorization flows through Core
```

#### ClimateDataSource Model
**Removed:** `created_by` ForeignKey to User

```python
# REMOVED LINE 56
created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='climate_sources')
```

**Impact:** Data sources are now system-wide resources. Authorization is determined by which Studies use them, and users who created those Studies have access.

#### ClimateDataRequest Model
**Removed:** `requested_by` ForeignKey to User

```python
# REMOVED LINE 267
requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
```

**Added:** Property methods for backward compatibility and clean authorization

```python
@property
def requested_by(self):
    """Get the user who owns the study (and thus this request).
    Authorization flows through the Core module via Study ownership.
    """
    return self.study.created_by if self.study else None

def user_can_access(self, user):
    """Check if a user can access this climate data request.
    Access is determined by Study ownership in the Core module.
    """
    return self.study.created_by == user if self.study else False
```

**Benefit:** Templates and code that reference `request.requested_by` continue to work, but now get the user through the Study relationship (Core module).

### 2. Form Updates (climate/forms.py)

#### ClimateDataConfigurationForm
**Removed:** Direct user assignment in save() method

```python
# BEFORE (line 125)
instance.requested_by = self.user

# AFTER (lines 125-126)
# User authorization flows through study.created_by (Core module)
# No need to set requested_by - accessed via property
```

### 3. View Updates (climate/views.py)

**No changes required!** Views were already correctly using `study__created_by` filtering:

- `climate_dashboard_view`: Uses `study__created_by=request.user` (line 41)
- `ClimateRequestListView`: Filters by `study__created_by=self.request.user` (line 139)
- `ClimateRequestDetailView`: Same pattern (line 151)
- `climate_data_export_view`: Uses `study__created_by=request.user` (line 185)
- All HTMX partials: Already correctly filtered (line 327)

### 4. Template Updates

**No changes required!** Grep search confirmed no templates directly reference `requested_by` field.

### 5. Migration Created

**File:** `climate/migrations/0002_remove_direct_user_relationships.py`

Operations:
- Remove `requested_by` field from `ClimateDataRequest`
- Remove `created_by` field from `ClimateDataSource`

## Architecture Benefits

### Before (Incorrect)
```
User ← requested_by ─ ClimateDataRequest
User ← created_by ─── ClimateDataSource
```

**Problem:** Climate module directly depends on Users module, bypassing Core

### After (Correct)
```
User → created_by → Study ← study ─ ClimateDataRequest
                    Study ────────── ClimateDataSource (via usage)
```

**Solution:** All authorization flows through Study ownership in Core module

## How It Works Now

### Creating a Climate Request
1. User creates/owns a Study (in Core module)
2. Study has `needs_climate_linkage=True` flag
3. User creates ClimateDataRequest for their Study
4. ClimateDataRequest.study links to the Study
5. Authorization: `request.study.created_by == user`

### Accessing Climate Data
1. Views filter requests: `ClimateDataRequest.objects.filter(study__created_by=request.user)`
2. User can only see requests for Studies they own
3. Templates can still use `request.requested_by` (property method)
4. Authorization check: `request.user_can_access(user)` method

### Benefits
1. **Single Source of Truth:** All authorization through Study ownership
2. **Simpler Permissions:** Check `study.created_by` instead of multiple user fields
3. **Better Modularity:** Climate module independent of User model
4. **Cleaner Architecture:** Follows Core-centric design pattern
5. **Easier to Extend:** New modules follow same pattern

## Testing Checklist

Before deploying to production, verify:

- [ ] Users can only see their own climate requests
- [ ] Users can only create requests for their own studies
- [ ] Climate request list view filters correctly
- [ ] Climate request detail view checks permissions properly
- [ ] CSV export requires ownership
- [ ] HTMX partials respect authorization
- [ ] Admin interface works correctly
- [ ] No broken template references
- [ ] Migration runs successfully
- [ ] Existing data preserved (if any)

## Deployment Steps

1. **Review Changes**
   ```bash
   git diff climate/
   ```

2. **Run Tests** (when available)
   ```bash
   python manage.py test climate
   ```

3. **Apply Migration**
   ```bash
   python manage.py migrate climate
   ```

4. **Verify Authorization**
   - Test as different users
   - Ensure users only see their own requests
   - Verify create/view/export permissions

5. **Monitor Logs**
   - Check for any AttributeError on requested_by
   - Verify study relationships are correct

## Files Modified

### Climate Module Only (Core unchanged)
1. `climate/models.py` - Removed User ForeignKeys, added property methods
2. `climate/forms.py` - Removed direct user assignment
3. `climate/migrations/0002_remove_direct_user_relationships.py` - New migration

### Documentation Created
1. `docs/architecture_diagram.svg` - Clean architecture diagram
2. `docs/architecture_corrected.svg` - Before/after comparison
3. `docs/ARCHITECTURE_FIXES.md` - Detailed fix documentation
4. `docs/CLIMATE_MODULE_ARCHITECTURE_FIX.md` - This file

## Architecture Diagram

See `docs/architecture_diagram.svg` for visual representation showing:
- Core module as central hub
- Climate module connecting through Study
- Authorization flowing through study.created_by
- Data storage via shared Observation model
- Integration with Health and Geolocation modules

## Key Takeaway

**All module interactions now flow through the Core module.** The Climate module no longer has direct relationships with the Users module. Authorization is determined by Study ownership in Core, making the architecture cleaner, more maintainable, and easier to extend.
