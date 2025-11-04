# HarmonAIze Climate Module - SVG Slide Deck

This directory contains a comprehensive set of SVG slides documenting the HarmonAIze Climate Module architecture, design, and implementation.

## Slide Overview

### Slide 1: Climate Module Overview (FIXED)
**File:** `slide1-overview.svg`

**Content:**
- Title: "HarmonAIze Climate Module"
- Purpose statement and supported data sources
- 4-box feature grid highlighting:
  - Multi-Source Support (MS)
  - Smart Caching (SC)
  - Progress Tracking (PT)
  - Mock Mode (MM)
- Component layer diagram showing Models, Services, Views, and Tasks

**Key Design Decisions:**
- Removed external Google Fonts dependency
- Replaced emoji icons with simple letter-based icons
- Used solid colors instead of complex gradients
- Web-safe fonts only (Arial, Helvetica, sans-serif)

---

### Slide 2: Architecture Overview
**File:** `slide2-architecture.svg`

**Content:** (Existing slide - not modified)
- System architecture diagram
- Component relationships

---

### Slide 3: Data Flow
**File:** `slide3-data-flow.svg`

**Content:** (Existing slide - not modified)
- Data processing pipeline
- Flow between components

---

### Slide 4: Integration
**File:** `slide4-integration.svg`

**Content:** (Existing slide - not modified)
- Integration with core HarmonAIze system
- External API connections

---

### Slide 5: Service Layer Deep Dive (NEW)
**File:** `slide5-service-layer.svg`

**Content:**
- Service class hierarchy with inheritance diagram
  - BaseClimateDataService (abstract base)
  - EarthEngineDataService implementation
  - CopernicusDataService implementation
- ClimateDataProcessor orchestration flow
- SpatioTemporalMatcher for location matching
- Method signatures and key responsibilities
- API authentication flow for:
  - Google Earth Engine (service account)
  - Copernicus CDS (API key)
  - Mock Mode (testing)
  - Error handling strategies

**Visual Elements:**
- Color-coded service boxes (green for GEE, blue for Copernicus)
- Dashed inheritance lines showing abstract base class pattern
- Flow arrows showing data processing pipeline
- Authentication panels with step-by-step setup

---

### Slide 6: Data Models & Relationships (NEW)
**File:** `slide6-data-models.svg`

**Content:**
- Complete entity-relationship diagram showing:
  - **ClimateDataSource** (blue) - Data provider configuration
  - **ClimateVariable** (green) - Climate variable definitions
  - **ClimateVariableMapping** (purple) - M2M mapping table
  - **ClimateDataRequest** (dark blue) - Request tracking
  - **ClimateDataCache** (dark green) - Caching layer
  - **Study** (gray, dashed) - Core module reference
- All foreign key relationships with cardinalities (1:M, M:M)
- Field details including primary keys, data types
- Unique constraints and indexes

**Visual Elements:**
- Color-coded models by functional area
- Arrows showing FK relationships
- Dashed arrows for M2M relationships
- Legend explaining notation
- Constraint annotations (e.g., cache_key unique constraint)

---

### Slide 7: Configuration & Deployment (NEW)
**File:** `slide7-configuration.svg`

**Content:**
- **Left Column:** Django Settings
  - LOCAL_APPS configuration
  - URL routing setup
  - Celery task configuration with beat schedule
- **Right Column:** Environment & API Credentials
  - Google Earth Engine service account setup
  - Copernicus CDS API credentials
  - Mock Mode toggle for development
- **Bottom:** API Credentials Setup Flow
  - 6-step process from registration to production
  - Success/failure decision flow
  - Mock mode bypass option

**Visual Elements:**
- Two-column layout for organized content
- Color-coded panels (blue=Django, green=GEE, cyan=CDS, orange=Mock)
- Flowchart with numbered steps
- Configuration code examples in monospace font

---

### Slide 8: Request Processing Lifecycle (NEW)
**File:** `slide8-request-lifecycle.svg`

**Content:**
- **Top:** State transition flow diagram
  - PENDING → PROCESSING → COMPLETED
  - PENDING → PROCESSING → FAILED
  - Cache hit fast path
  - Retry logic (max 3 attempts)
- **Bottom:** Three detailed panels
  - Progress Tracking (real-time updates, WebSocket support)
  - Cache Strategy (decision points, expiration)
  - Error Handling (retry logic, notifications)

**Visual Elements:**
- Color-coded states (yellow=pending, blue=processing, green=completed, red=failed)
- Decision diamonds for cache hits and success checks
- Timing estimates for each stage
- Success (green) and error (red) paths
- Dashed orange line for retry flow

**Performance Notes:**
- PENDING: ~1-2 seconds (validation, cache check)
- PROCESSING: ~30-120 seconds (API calls, bottleneck)
- COMPLETED: ~2-5 seconds (caching, observation creation)
- FAILED: ~1 second (error logging)

---

## Technical Specifications

### SVG Standards Compliance

All slides follow these standards for maximum compatibility:

1. **XML Declaration:** Proper `<?xml version="1.0" encoding="UTF-8" standalone="no"?>` header
2. **Namespace:** Correct SVG namespace `xmlns="http://www.w3.org/2000/svg"`
3. **ViewBox:** Consistent `viewBox="0 0 1280 720"` for 16:9 aspect ratio
4. **Dimensions:** Explicit `width="1280" height="720"` attributes
5. **Fonts:** Web-safe fonts only (Arial, Helvetica, sans-serif)
6. **No External Dependencies:** All resources embedded
7. **No Emojis:** Text-based or simple geometric icons only
8. **Simple Styling:** Solid colors preferred, minimal gradients

### Color Palette

The slides use a consistent climate-themed color palette:

- **Primary Blues:** `#2196F3` (light), `#1976D2` (medium), `#1565C0` (dark)
- **Primary Greens:** `#4CAF50` (light), `#388E3C` (medium), `#2E7D32` (dark)
- **Earth Tones:** `#607D8B` (gray-blue), `#795548` (brown)
- **Accent Colors:**
  - Warning: `#FFC107` (amber)
  - Error: `#F44336` (red)
  - Info: `#00BCD4` (cyan)
  - Purple: `#9C27B0` (for M2M relationships)
  - Orange: `#FF9800` (for configuration/settings)

### Typography Hierarchy

- **Slide Titles:** 48px, bold, `#1e3a5f`
- **Section Titles:** 20-28px, bold, `#2c5282`
- **Body Text:** 13-18px, regular, `#2d3748`
- **Small Text:** 11-12px, regular, `#4a5568`
- **Code/Config:** 12px, monospace, `#2d3748`

## Viewing the Slides

### Recommended Viewers

These slides are tested and compatible with:

1. **Web Browsers:**
   - Chrome/Edge (Chromium)
   - Firefox
   - Safari

2. **SVG Editors:**
   - Figma (File → Import)
   - Adobe Illustrator
   - Inkscape

3. **Code Editors:**
   - VS Code (with SVG preview extension)
   - Sublime Text

4. **Presentation Tools:**
   - Can be embedded in HTML presentations
   - Can be converted to PNG for PowerPoint/Keynote

### Opening in Browser

```bash
# Open all slides in default browser (macOS)
open slide*.svg

# Open specific slide
open slide1-overview.svg
```

### Converting to PNG (Optional)

If you need raster images for presentations:

```bash
# Using Inkscape CLI (if installed)
inkscape slide1-overview.svg --export-type=png --export-dpi=150

# Using ImageMagick (if installed)
convert -density 150 slide1-overview.svg slide1-overview.png

# Using rsvg-convert (if installed)
rsvg-convert -w 1920 slide1-overview.svg > slide1-overview.png
```

## Editing the Slides

### Direct SVG Editing

The SVG files are well-structured for editing:

1. **Clear groups:** Each section is wrapped in `<g id="...">` tags
2. **Semantic IDs:** Groups have descriptive names
3. **CSS classes:** Consistent styling via classes
4. **Comments:** XML comments mark major sections

### Using Design Tools

**Figma:**
1. File → Import → Select SVG
2. Layers will be preserved
3. Edit text, colors, positions
4. Export as SVG or PNG

**Adobe Illustrator:**
1. File → Open → Select SVG
2. All elements editable
3. Can export back to SVG

**Inkscape:**
1. File → Open → Select SVG
2. Native SVG editor
3. Full control over elements

## Presentation Tips

1. **Sequence:** Present slides in order (1→2→3→4→5→6→7→8)
2. **Timing:** Allow 2-3 minutes per slide
3. **Focus Areas:**
   - Slide 1: Overview and value proposition
   - Slide 5: Technical implementation depth
   - Slide 6: Data model relationships
   - Slide 7: Practical setup guide
   - Slide 8: Runtime behavior and performance

4. **Interactive Elements:**
   - Zoom into detailed sections
   - Highlight specific components
   - Trace data flow paths
   - Point out error handling

## Maintenance

When updating the slides:

1. **Preserve Standards:** Keep XML declaration and proper namespace
2. **Maintain Colors:** Use the established color palette
3. **Test Compatibility:** Open in multiple viewers
4. **Update This README:** Document any changes
5. **Version Control:** Commit SVG files directly to git

## File Sizes

| Slide | Size | Complexity |
|-------|------|------------|
| slide1-overview.svg | 7.6K | Simple |
| slide2-architecture.svg | 11K | Medium |
| slide3-data-flow.svg | 10K | Medium |
| slide4-integration.svg | 11K | Medium |
| slide5-service-layer.svg | 11K | Medium |
| slide6-data-models.svg | 12K | High |
| slide7-configuration.svg | 9.5K | Medium |
| slide8-request-lifecycle.svg | 11K | High |

**Total:** ~93K for complete deck (tiny compared to PDF or PPTX!)

## License

These slides are part of the HarmonAIze project and follow the same license as the main project.

---

**Created:** November 4, 2025
**Last Updated:** November 4, 2025
**Maintainer:** HarmonAIze Development Team
