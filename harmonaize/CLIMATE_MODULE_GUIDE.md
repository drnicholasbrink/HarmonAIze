# Climate Module User Guide

## Where Are the Dropdown Categories?

### Location 1: Django Admin - Add/Edit Climate Variable

**URL:** http://localhost:8000/admin/climate/climatevariable/add/

**Steps to See Dropdowns:**
1. Login to admin: http://localhost:8000/admin
   - Email: `admin@harmonaize.local`
   - Password: `admin123`

2. Navigate to: **Climate → Climate Variables**

3. Click **"ADD CLIMATE VARIABLE +"** button (top right)

4. **The "Category" field is a DROPDOWN** with 12 predefined options:
   - Temperature
   - Precipitation
   - Humidity & Moisture
   - Wind
   - Atmospheric Pressure
   - Solar Radiation
   - Evapotranspiration
   - Air Quality
   - Vegetation Indices
   - Cloud Cover
   - Extreme Events
   - Other

5. Fill in the form:
   - **Name**: Technical name (e.g., `temperature_2m`)
   - **Display name**: Human-readable (e.g., `2-meter Temperature`)
   - **Description**: What this variable measures
   - **Category**: ← **SELECT FROM DROPDOWN** ←
   - **Unit**: e.g., `degrees Celsius`
   - **Unit symbol**: e.g., `°C`

6. Click **SAVE**

### Location 2: Editing Existing Variables

**URL:** http://localhost:8000/admin/climate/climatevariable/

1. Click on any existing variable (e.g., "Dewpoint Temperature")
2. Scroll to the "Category" field
3. You'll see it's a **dropdown showing current selection** with all 12 options available

---

## How to Make Climate Data Requests (Live API Calls)

### Method 1: Through the Web Interface

#### Step 1: Navigate to Climate Dashboard
**URL:** http://localhost:8000/climate/

#### Step 2: Find a Study
- Look at "Studies Ready for Climate Data" panel
- You should see: "Temperature and Health Outcomes Study"
- Click **"Configure Climate Data"** button

#### Step 3: Configure Request
**URL:** http://localhost:8000/climate/configure/1/ (or configure/2/)

On this page you'll:
1. **Select Climate Variables** - Check boxes for variables you want
   - The variables are **grouped by category** (the dropdowns you defined!)
   - Example: All temperature variables appear under "Temperature" section

2. **Select Data Source** - Choose which API to use:
   - CHIRPS Precipitation
   - ERA5 Reanalysis
   - MODIS Terra

3. **Set Date Range** - Pick start and end dates

4. **Click "Request Climate Data"** - This submits the API request!

#### Step 4: View Request
**URL:** http://localhost:8000/climate/requests/

- See your request with status: Pending → Processing → Completed
- Click on a request to see details
- Export results when completed

---

## Current Test Data Available

### Studies (2)
1. **Temperature and Health Outcomes Study** (ID: 1)
2. **Temperature and Health Outcomes Study** (ID: 2)

### Locations (6 South African Cities)
1. Johannesburg CBD: (-26.2041, 28.0473)
2. Soweto: (-26.2678, 27.8585)
3. Sandton: (-26.1076, 28.0567)
4. Pretoria: (-25.7479, 28.2293)
5. Cape Town: (-33.9249, 18.4241)
6. Durban: (-29.8587, 31.0218)

### Climate Data Sources (3)
1. **CHIRPS Precipitation** (Active)
2. **ERA5 Reanalysis** (Active)
3. **MODIS Terra** (Active)

### Climate Variables (10 - All with Dropdown Categories)
1. **Dewpoint Temperature** - Category: Humidity & Moisture
2. **Daily Precipitation** - Category: Precipitation
3. **Total Precipitation** - Category: Precipitation
4. **Surface Solar Radiation** - Category: Solar Radiation
5. **2m Temperature** - Category: Temperature
6. **Land Surface Temperature (Day)** - Category: Temperature
7. **Maximum Temperature** - Category: Temperature
8. **Minimum Temperature** - Category: Temperature
9. **U-component Wind (10m)** - Category: Wind
10. **V-component Wind (10m)** - Category: Wind

---

## Quick Navigation Links

| Page | URL | Purpose |
|------|-----|---------|
| **Climate Dashboard** | http://localhost:8000/climate/ | Main climate module interface |
| **Configure for Study** | http://localhost:8000/climate/configure/1/ | Set up climate data request |
| **View All Requests** | http://localhost:8000/climate/requests/ | See all API requests |
| **Admin: Climate Variables** | http://localhost:8000/admin/climate/climatevariable/ | **← DROPDOWNS HERE** |
| **Admin: Data Sources** | http://localhost:8000/admin/climate/climatedatasource/ | Manage APIs |
| **Admin: Add Variable** | http://localhost:8000/admin/climate/climatevariable/add/ | **← USE DROPDOWNS** |

---

## Visual Guide: Where to Find Dropdowns

```
Admin Interface
├── Climate
│   ├── Climate Variables  ← Click here
│   │   ├── List of variables (shows category in list)
│   │   └── Add Climate Variable  ← DROPDOWN IS HERE!
│   │       └── Form Fields:
│   │           ├── Name (text)
│   │           ├── Display name (text)
│   │           ├── Description (textarea)
│   │           ├── Category  ← **DROPDOWN WITH 12 OPTIONS**
│   │           ├── Unit (text)
│   │           └── Unit symbol (text)
```

---

## Demo Flow for Your Meeting

### Part 1: Show Dropdown Categories (2 minutes)
1. Go to: http://localhost:8000/admin/climate/climatevariable/
2. Click "ADD CLIMATE VARIABLE +"
3. **Click on the "Category" dropdown** - Show all 12 options
4. Explain: "These are prescriptive categories that ensure API compatibility"
5. Click on existing variable to show it uses dropdown too

### Part 2: Show Live API Request (3 minutes)
1. Go to: http://localhost:8000/climate/
2. Show dashboard overview
3. Click "Configure Climate Data" for a study
4. **Show how variables are grouped by category** (your dropdowns in action!)
5. Select some variables and a data source
6. Submit request
7. Go to requests list to show status

### Part 3: Show Core Integration (2 minutes)
1. Explain: "All climate data becomes Attributes in Core"
2. Show in admin: Climate data stored as Observations
3. Query-able alongside health data through Core's unified API

---

## Troubleshooting

**Q: I don't see the dropdown?**
- Make sure you're logged in to admin
- Navigate to: Climate → Climate Variables → Add
- The "Category" field should be a dropdown (not a text field)

**Q: How do I know the categories are working?**
- Look at the variable list - it shows "Category" column
- Edit any variable - you'll see the dropdown
- When configuring climate data, variables are grouped by category

**Q: Can I change categories after creating variables?**
- Yes! Edit the variable and select a different category from the dropdown
- The migration will map any old custom categories to the new predefined ones
