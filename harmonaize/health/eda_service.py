"""
Privacy-aware exploratory data analysis service with interactive Plotly visualizations.
"""
from __future__ import annotations

import io
import base64
import warnings
import logging
from pathlib import Path
from collections import Counter
from typing import Any, Optional, Dict, List
from datetime import timedelta

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.offline import plot as plotly_plot

logger = logging.getLogger(__name__)

# Try importing matplotlib for word cloud generation
try:
    import matplotlib
    matplotlib.use("Agg")  # Use non-interactive backend
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    MATPLOTLIB_AVAILABLE = False

import re

try:
    from wordcloud import WordCloud
    WORDCLOUD_AVAILABLE = True
except ImportError:
    WORDCLOUD_AVAILABLE = False

PRIVACY_MIN_ROWS = 20
PRIVACY_MIN_GROUP_COUNT = 3
MAX_HISTOGRAM_BINS = 12
MAX_WORD_CLOUD_TERMS = 40
MAX_CATEGORICAL_VALUES = 20  # Increased from 10 for more thorough analysis
MAX_CORRELATION_COLUMNS = 12
# Threshold for switching from categorical visualization to word cloud/text analysis
WORD_CLOUD_MIN_UNIQUE = 25  # If a text column has >= this many unique non-null values, treat as high-cardinality text
TOKEN_REGEX = re.compile(r"[A-Za-z]{3,}")
STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "from",
    "this",
    "have",
    "were",
    "your",
    "their",
    "about",
    "there",
    "which",
    "would",
    "could",
    "should",
    "cannot",
    "patient",
    "study",
}

PII_KEYWORDS = {
    "patient",
    "identifier",
    "identity",
    "passport",
    "national",
    "mrn",
    "contact",
    "email",
    "phone",
    "address",
    "location",
    "latitude",
    "longitude",
    "lat",
    "lon",
    "geo",
    "facility",
    "clinic",
    "surname",
    "firstname",
    "fullname",
}

PII_SUFFIXES = ("_id", "_name", "_identifier")
PII_EXACT = {"id", "name", "patient", "patient_id", "location"}


def _format_number(value: float) -> Optional[float]:
    if value is None:
        return None
    if pd.isna(value):
        return None
    return round(float(value), 3)


def _read_dataframe(file_path: Path, file_format: str) -> pd.DataFrame:
    """Read a dataframe using the full dataset with format-specific handlers."""
    if file_format == "csv":
        return pd.read_csv(file_path)
    if file_format in {"xlsx", "xls"}:
        return pd.read_excel(file_path)
    if file_format == "json":
        return pd.read_json(file_path, lines=True)
    if file_format == "txt":
        return pd.read_csv(file_path, sep="\t")
    # Attempt CSV as a final fallback
    return pd.read_csv(file_path)


def _safe_numeric(series: pd.Series) -> pd.Series:
    """Return numeric representation ignoring non-numeric entries."""
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.dropna()


def _histogram_for(series: pd.Series) -> Optional[Dict[str, Any]]:
    values = series.dropna().tolist()
    if len(values) < PRIVACY_MIN_ROWS:
        return None
    bins = min(MAX_HISTOGRAM_BINS, max(5, int(np.sqrt(len(values)))))
    counts, bin_edges = np.histogram(values, bins=bins)
    if any(c < PRIVACY_MIN_GROUP_COUNT for c in counts):
        return None
    return {
        "bins": [round(float(edge), 4) for edge in bin_edges.tolist()],
        "counts": counts.tolist(),
    }


def _categorical_summary(series: pd.Series) -> List[Dict[str, Any]]:
    """Generate privacy-aware categorical summary with top values."""
    counts = series.dropna().value_counts()
    results: List[Dict[str, Any]] = []
    total = int(counts.sum())
    
    if total < PRIVACY_MIN_ROWS:
        return results
    
    # Get top N values that meet privacy threshold
    for value, count in counts.head(MAX_CATEGORICAL_VALUES).items():
        if count < PRIVACY_MIN_GROUP_COUNT:
            continue
        results.append(
            {
                "value": str(value)[:100],  # Truncate very long values
                "count": int(count),
                "percentage": round(float(count / total * 100), 2),
            }
        )
    
    # If we have very few results due to privacy filtering, return empty to avoid misleading viz
    if len(results) < 2:
        return []
    
    return results


def _tokenise_text(series: pd.Series) -> List[Dict[str, Any]]:
    values = series.dropna().astype(str).tolist()
    logger.debug("_tokenise_text: processing %d values", len(values))
    if not values:
        logger.debug("_tokenise_text: no values to process")
        return []
    tokens: Counter[str] = Counter()
    for value in values:
        matches = TOKEN_REGEX.findall(value)
        for token in matches:
            lowered = token.lower()
            if lowered in STOP_WORDS:
                continue
            tokens[lowered] += 1
    logger.debug("_tokenise_text: found %d raw tokens", len(tokens))
    filtered: Dict[str, int] = {
        token: count for token, count in tokens.items() if count >= PRIVACY_MIN_GROUP_COUNT
    }
    logger.debug("_tokenise_text: %d tokens after privacy filter (min count=%d)", len(filtered), PRIVACY_MIN_GROUP_COUNT)
    if not filtered:
        logger.debug("_tokenise_text: no tokens survived privacy filtering")
        return []
    top_items = sorted(filtered.items(), key=lambda item: item[1], reverse=True)[:MAX_WORD_CLOUD_TERMS]
    max_count = top_items[0][1]
    logger.debug("_tokenise_text: returning %d tokens, top token '%s' has count %d", len(top_items), top_items[0][0], max_count)
    return [
        {
            "token": token,
            "count": count,
            "weight": round(count / max_count, 4),
        }
        for token, count in top_items
    ]


def _generate_word_cloud_image(tokens: List[Dict[str, Any]]) -> Optional[str]:
    """
    Generate a word cloud image from token data using the wordcloud library.
    Returns a base64-encoded PNG image string, or None if generation fails.
    """
    logger.debug("_generate_word_cloud_image: starting with %d tokens, wordcloud_available=%s", len(tokens) if tokens else 0, WORDCLOUD_AVAILABLE)
    
    if not WORDCLOUD_AVAILABLE:
        logger.warning("_generate_word_cloud_image: wordcloud library not available")
        return None
        
    if not tokens:
        logger.warning("_generate_word_cloud_image: no tokens provided")
        return None
    
    try:
        # Create frequency dictionary for WordCloud
        frequencies = {item["token"]: item["count"] for item in tokens}
        logger.debug("_generate_word_cloud_image: created frequency dict with %d terms, top term: %s (count=%d)", 
                    len(frequencies), max(frequencies.keys(), key=frequencies.get), max(frequencies.values()))
        
        # Generate word cloud with custom styling
        wc = WordCloud(
            width=800,
            height=400,
            background_color="white",
            colormap="viridis",
            relative_scaling=0.5,
            min_font_size=10,
            max_font_size=80,
            prefer_horizontal=0.7,
            scale=2,
            margin=10,
        ).generate_from_frequencies(frequencies)
        
        logger.debug("_generate_word_cloud_image: wordcloud object created successfully")
        
        # Convert to base64-encoded PNG
        img_buffer = io.BytesIO()
        wc.to_image().save(img_buffer, format="PNG")
        img_buffer.seek(0)
        img_data = img_buffer.read()
        img_size = len(img_data)
        img_base64 = base64.b64encode(img_data).decode("utf-8")
        
        logger.debug("_generate_word_cloud_image: successfully generated base64 image, size=%d bytes, base64_length=%d", 
                    img_size, len(img_base64))
        
        return img_base64
    except Exception as e:  # pragma: no cover - defensive fallback
        logger.error("_generate_word_cloud_image: failed to generate word cloud image: %s", str(e), exc_info=True)
        return None


def _generate_categorical_chart_image(labels: List[str], counts: List[int], percentages: List[float], column_name: str) -> Optional[str]:
    """
    Generate a horizontal bar chart image for categorical data using matplotlib.
    Returns a base64-encoded PNG image string, or None if generation fails.
    """
    if not MATPLOTLIB_AVAILABLE or not labels or not counts:
        return None
    
    try:
        # Create figure with custom styling
        fig, ax = plt.subplots(figsize=(10, max(6, len(labels) * 0.4)))
        fig.patch.set_facecolor('white')
        fig.patch.set_alpha(0.0)
        
        # Create horizontal bar chart
        y_pos = np.arange(len(labels))
        bars = ax.barh(y_pos, counts, color='#34C759', alpha=0.8, edgecolor='#2CA64A', linewidth=1.5)
        
        # Customize axes
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=10)
        ax.set_xlabel('Count', fontsize=11, fontweight='bold')
        ax.set_title(f'{column_name} - Distribution', fontsize=13, fontweight='bold', pad=15)
        
        # Add value labels on bars
        for i, (bar, count, pct) in enumerate(zip(bars, counts, percentages)):
            width = bar.get_width()
            ax.text(width, bar.get_y() + bar.get_height()/2, 
                   f' {count} ({pct:.1f}%)',
                   va='center', fontsize=9, fontweight='bold')
        
        # Style the plot
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#CCCCCC')
        ax.spines['bottom'].set_color('#CCCCCC')
        ax.grid(axis='x', alpha=0.3, linestyle='--', linewidth=0.5)
        ax.set_axisbelow(True)
        
        # Invert y-axis so highest values are at top
        ax.invert_yaxis()
        
        # Tight layout
        plt.tight_layout()
        
        # Convert to base64-encoded PNG
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='PNG', dpi=100, bbox_inches='tight', facecolor='white', edgecolor='none')
        plt.close(fig)
        img_buffer.seek(0)
        img_base64 = base64.b64encode(img_buffer.read()).decode('utf-8')
        
        return img_base64
    except Exception:  # pragma: no cover - defensive fallback
        return None



def _generate_numeric_dashboard(series: pd.Series, column_name: str, stats: dict[str, Any]) -> Optional[str]:
    """
    Generate an interactive Plotly dashboard for numeric variables.
    Creates multiple visualizations: histogram, box plot, violin plot, and KDE.
    Returns HTML div string for embedding with standalone mode for expandability.
    """
    try:
        from plotly.subplots import make_subplots
        
        # Create subplots: 2 rows, 2 columns
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                "Distribution",
                "Box & Whisker",
                "Violin Plot with Points", 
                "Cumulative Distribution"
            ),
            specs=[
                [{"type": "histogram"}, {"type": "box"}],
                [{"type": "violin"}, {"type": "scatter"}]
            ],
            vertical_spacing=0.12,
            horizontal_spacing=0.10,
        )
        
        # 1. Histogram with better binning
        fig.add_trace(
            go.Histogram(
                x=series,
                name="Frequency",
                marker_color="#007AFF",
                opacity=0.75,
                nbinsx=30,
                showlegend=False,
                hovertemplate="<b>Range:</b> %{x}<br><b>Count:</b> %{y}<extra></extra>",
            ),
            row=1, col=1,
        )
        
        # 2. Box Plot - only show outliers, not all whisker endpoints
        fig.add_trace(
            go.Box(
                y=series,
                name=column_name,
                marker_color="#34C759",
                boxmean="sd",  # Show mean and standard deviation
                boxpoints="outliers",  # Only show outlier points (beyond whiskers)
                showlegend=False,
                hovertemplate="<b>Value:</b> %{y}<extra></extra>",
            ),
            row=1, col=2,
        )
        
        # 3. Violin Plot - no individual points, just the distribution
        fig.add_trace(
            go.Violin(
                y=series,
                name=column_name,
                marker_color="#5AC8FA",
                box_visible=True,
                meanline_visible=True,
                points=False,  # Remove all points for cleaner visualization
                showlegend=False,
                hovertemplate="<b>Value:</b> %{y}<br><extra></extra>",
            ),
            row=2, col=1,
        )
        
        # 4. Cumulative Distribution Function (CDF)
        sorted_data = np.sort(series.dropna())
        cumulative_prob = np.arange(1, len(sorted_data) + 1) / len(sorted_data)
        
        fig.add_trace(
            go.Scatter(
                x=sorted_data,
                y=cumulative_prob,
                mode="lines",
                line=dict(color="#FF9500", width=3),
                name="CDF",
                showlegend=False,
                hovertemplate="<b>Value:</b> %{x}<br><b>Percentile:</b> %{y:.1%}<extra></extra>",
            ),
            row=2, col=2,
        )
        
        # Update layout
        fig.update_layout(
            title={
                "text": f"<b>{column_name}</b> - Interactive Analysis",
                "x": 0.5,
                "xanchor": "center",
                "font": {"size": 18},
            },
            height=800,
            autosize=True,
            showlegend=False,
            template="plotly_white",
            margin=dict(l=60, r=60, t=100, b=60),
            hovermode="closest",
        )
        
        # Update axes labels
        fig.update_xaxes(title_text="Value", row=1, col=1)
        fig.update_yaxes(title_text="Frequency", row=1, col=1)
        fig.update_yaxes(title_text="Value", row=1, col=2)
        fig.update_yaxes(title_text="Value", row=2, col=1)
        fig.update_xaxes(title_text="Value", row=2, col=2)
        fig.update_yaxes(title_text="Cumulative Probability", row=2, col=2)
        
        # Convert to HTML div with config for better interactivity
        config = {
            "displayModeBar": True,
            "displaylogo": False,
            "modeBarButtonsToAdd": ["drawline", "drawopenpath", "eraseshape"],
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
            "toImageButtonOptions": {
                "format": "png",
                "filename": f"{column_name}_analysis",
                "height": 750,
                "width": 1200,
                "scale": 2,
            },
        }
        
        plot_div = plotly_plot(fig, output_type="div", include_plotlyjs="cdn", config=config)
        return plot_div
        
    except Exception as e:
        # Fallback to None if generation fails
        return None


def _generate_categorical_dashboard(
    labels: list[str], 
    counts: list[int], 
    percentages: list[float], 
    column_name: str,
    total_count: int,
    unique_count: int,
    missing_count: int,
) -> Optional[str]:
    """
    Generate an interactive Plotly dashboard for categorical variables.
    Creates multiple visualizations: bar chart, pie chart, treemap, and sunburst.
    Returns HTML div string for embedding with standalone mode for expandability.
    """
    try:
        from plotly.subplots import make_subplots
        
        # Create subplots
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                "Frequency Distribution",
                "Proportions (Pie Chart)",
                "Hierarchical View (Treemap)",
                "Ranked Distribution",
            ),
            specs=[
                [{"type": "bar"}, {"type": "pie"}],
                [{"type": "treemap"}, {"type": "bar"}],
            ],
            vertical_spacing=0.15,
            horizontal_spacing=0.10,
        )
        
        # Color palette
        colors = px.colors.qualitative.Set3[:len(labels)]
        
        # 1. Vertical Bar Chart
        fig.add_trace(
            go.Bar(
                x=labels,
                y=counts,
                marker_color=colors,
                text=[f"{c:,}" for c in counts],
                textposition="outside",
                showlegend=False,
                hovertemplate="<b>%{x}</b><br>Count: %{y:,}<br>Percentage: %{customdata:.1f}%<extra></extra>",
                customdata=percentages,
            ),
            row=1, col=1,
        )
        
        # 2. Pie Chart
        fig.add_trace(
            go.Pie(
                labels=labels,
                values=counts,
                marker_colors=colors,
                textinfo="label+percent",
                hovertemplate="<b>%{label}</b><br>Count: %{value:,}<br>Percentage: %{percent}<extra></extra>",
                showlegend=False,
            ),
            row=1, col=2,
        )
        
        # 3. Treemap
        fig.add_trace(
            go.Treemap(
                labels=labels,
                parents=[""] * len(labels),
                values=counts,
                text=[f"{l}<br>{c:,} ({p:.1f}%)" for l, c, p in zip(labels, counts, percentages)],
                textposition="middle center",
                marker=dict(
                    colorscale="Blues",
                    showscale=False,
                ),
                hovertemplate="<b>%{label}</b><br>Count: %{value:,}<extra></extra>",
            ),
            row=2, col=1,
        )
        
        # 4. Horizontal Bar Chart (sorted by count descending)
        sorted_indices = sorted(range(len(counts)), key=lambda i: counts[i], reverse=True)
        sorted_labels = [labels[i] for i in sorted_indices]
        sorted_counts = [counts[i] for i in sorted_indices]
        sorted_percentages = [percentages[i] for i in sorted_indices]
        
        fig.add_trace(
            go.Bar(
                y=sorted_labels,
                x=sorted_counts,
                orientation="h",
                marker_color="#34C759",
                text=[f"{c:,} ({p:.1f}%)" for c, p in zip(sorted_counts, sorted_percentages)],
                textposition="outside",
                showlegend=False,
                hovertemplate="<b>%{y}</b><br>Count: %{x:,}<extra></extra>",
            ),
            row=2, col=2,
        )
        
        # Update layout
        fig.update_layout(
            title={
                "text": f"<b>{column_name}</b> - Category Analysis<br><sub>{unique_count:,} unique | {total_count:,} total | {missing_count:,} missing</sub>",
                "x": 0.5,
                "xanchor": "center",
                "font": {"size": 18},
            },
            height=800,
            autosize=True,
            showlegend=False,
            template="plotly_white",
            margin=dict(l=60, r=60, t=100, b=60),
            hovermode="closest",
        )
        
        # Update axes
        fig.update_xaxes(title_text="Category", row=1, col=1, tickangle=-45)
        fig.update_yaxes(title_text="Count", row=1, col=1)
        fig.update_xaxes(title_text="Count", row=2, col=2)
        fig.update_yaxes(title_text="Category", row=2, col=2)
        
        # Invert y-axis for horizontal bar to show highest on top
        fig.update_yaxes(autorange="reversed", row=2, col=2)
        
        # Convert to HTML div with config
        config = {
            "displayModeBar": True,
            "displaylogo": False,
            "toImageButtonOptions": {
                "format": "png",
                "filename": f"{column_name}_categorical_analysis",
                "height": 800,
                "width": 1200,
                "scale": 2,
            },
        }
        
        plot_div = plotly_plot(fig, output_type="div", include_plotlyjs="cdn", config=config)
        return plot_div
        
    except Exception as e:
        return None


def _generate_word_cloud_dashboard(tokens: List[Dict[str, Any]], column_name: str) -> Optional[str]:
    """
    Generate an interactive Plotly dashboard for text/string variables.
    Creates focused text analysis visualizations without word cloud (that's kept separate).
    Returns HTML div string for embedding.
    """
    try:
        from plotly.subplots import make_subplots
        
        # Extract top tokens
        top_tokens = sorted(tokens, key=lambda x: x["count"], reverse=True)[:30]
        words = [t["token"] for t in top_tokens]
        counts = [t["count"] for t in top_tokens]
        
        # Create subplots - 2x2 grid
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                "Word Frequency Distribution",
                "Hierarchical Word Map",
                "Ranked Word Frequencies",
                "Cumulative Coverage",
            ),
            specs=[
                [{"type": "bar"}, {"type": "treemap"}],
                [{"type": "bar"}, {"type": "scatter"}],
            ],
            vertical_spacing=0.15,
            horizontal_spacing=0.10,
        )
        
        # Color scale for word frequency
        colors = px.colors.sequential.Blues[::-1]
        
        # 1. Vertical Bar Chart - Top 15 words
        fig.add_trace(
            go.Bar(
                x=words[:15],
                y=counts[:15],
                marker=dict(
                    color=counts[:15],
                    colorscale="Viridis",
                    showscale=False,
                ),
                text=[f"{c:,}" for c in counts[:15]],
                textposition="outside",
                showlegend=False,
                hovertemplate="<b>%{x}</b><br>Frequency: %{y:,}<extra></extra>",
            ),
            row=1, col=1,
        )
        
        # 2. Treemap - All top 30 words
        percentages = [(c / sum(counts)) * 100 for c in counts]
        fig.add_trace(
            go.Treemap(
                labels=words,
                parents=[""] * len(words),
                values=counts,
                text=[f"{w}<br>{c:,} ({p:.1f}%)" for w, c, p in zip(words, counts, percentages)],
                textposition="middle center",
                marker=dict(
                    colorscale="Blues",
                    showscale=False,
                ),
                hovertemplate="<b>%{label}</b><br>Frequency: %{value:,}<extra></extra>",
            ),
            row=1, col=2,
        )
        
        # 3. Horizontal Bar Chart - Top 15 words ranked
        fig.add_trace(
            go.Bar(
                y=words[:15][::-1],  # Reverse for top-to-bottom display
                x=counts[:15][::-1],
                orientation="h",
                marker_color="#34C759",
                text=[f"{c:,}" for c in counts[:15][::-1]],
                textposition="outside",
                showlegend=False,
                hovertemplate="<b>%{y}</b><br>Frequency: %{x:,}<extra></extra>",
            ),
            row=2, col=1,
        )
        
        # 4. Cumulative Distribution - Shows coverage by top N words
        cumulative_counts = [sum(counts[:i+1]) for i in range(len(counts))]
        total_count = sum(counts)
        cumulative_pct = [(cc / total_count) * 100 for cc in cumulative_counts]
        
        fig.add_trace(
            go.Scatter(
                x=list(range(1, len(words) + 1)),
                y=cumulative_pct,
                mode="lines+markers",
                name="Cumulative %",
                line=dict(color="#007AFF", width=3),
                marker=dict(size=6, color="#007AFF"),
                fill="tozeroy",
                fillcolor="rgba(0, 122, 255, 0.1)",
                hovertemplate="<b>Top %{x} words</b><br>Coverage: %{y:.1f}%<extra></extra>",
            ),
            row=2, col=2,
        )
        
        # Update layout
        fig.update_layout(
            title={
                "text": f"<b>{column_name}</b> - Text Analysis Dashboard",
                "x": 0.5,
                "xanchor": "center",
                "font": {"size": 18},
            },
            height=800,
            autosize=True,
            showlegend=False,
            template="plotly_white",
            margin=dict(l=60, r=60, t=100, b=60),
            hovermode="closest",
        )
        
        # Update axes
        fig.update_xaxes(title_text="Word", row=1, col=1, tickangle=-45)
        fig.update_yaxes(title_text="Frequency", row=1, col=1)
        fig.update_xaxes(title_text="Frequency", row=2, col=1)
        fig.update_yaxes(title_text="Word", row=2, col=1)
        fig.update_xaxes(title_text="Number of Words", row=2, col=2)
        fig.update_yaxes(title_text="Cumulative Coverage (%)", row=2, col=2)
        
        # Convert to HTML div with config
        config = {
            "displayModeBar": True,
            "displaylogo": False,
            "toImageButtonOptions": {
                "format": "png",
                "filename": f"{column_name}_text_analysis",
                "height": 800,
                "width": 1200,
                "scale": 2,
            },
        }
        
        plot_div = plotly_plot(fig, output_type="div", include_plotlyjs="cdn", config=config)
        return plot_div
        
    except Exception as e:
        return None


def _generate_numeric_histogram_image(bins: List[float], counts: List[int], column_name: str) -> Optional[str]:
    """
    Generate a histogram bar chart image for numeric data using matplotlib.
    Returns a base64-encoded PNG image string, or None if generation fails.
    """
    if not MATPLOTLIB_AVAILABLE or not bins or not counts:
        return None
    
    try:
        # Create figure with custom styling
        fig, ax = plt.subplots(figsize=(10, 5))
        fig.patch.set_facecolor('white')
        fig.patch.set_alpha(0.0)
        
        # Calculate bin centers for positioning bars
        bin_centers = [(bins[i] + bins[i+1]) / 2 for i in range(len(bins) - 1)]
        bin_widths = [bins[i+1] - bins[i] for i in range(len(bins) - 1)]
        
        # Create vertical bar chart
        bars = ax.bar(bin_centers, counts, width=bin_widths, color='#007AFF', alpha=0.7, 
                      edgecolor='#0051D5', linewidth=1.5, align='center')
        
        # Customize axes
        ax.set_xlabel('Value', fontsize=11, fontweight='bold')
        ax.set_ylabel('Frequency', fontsize=11, fontweight='bold')
        ax.set_title(f'{column_name} - Distribution', fontsize=13, fontweight='bold', pad=15)
        
        # Style the plot
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#CCCCCC')
        ax.spines['bottom'].set_color('#CCCCCC')
        ax.grid(axis='y', alpha=0.3, linestyle='--', linewidth=0.5)
        ax.set_axisbelow(True)
        
        # Format x-axis labels based on value range
        max_val = max(bins)
        if max_val < 100:
            ax.ticklabel_format(style='plain', axis='x')
        else:
            ax.ticklabel_format(style='sci', axis='x', scilimits=(0,0))
        
        # Tight layout
        plt.tight_layout()
        
        # Convert to base64-encoded PNG
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='PNG', dpi=100, bbox_inches='tight', facecolor='white', edgecolor='none')
        plt.close(fig)
        img_buffer.seek(0)
        img_base64 = base64.b64encode(img_buffer.read()).decode('utf-8')
        
        return img_base64
    except Exception:  # pragma: no cover - defensive fallback
        return None


def _correlation_matrix(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    numeric_df = df.select_dtypes(include=["number"]).dropna(axis=1, how="all")
    if numeric_df.shape[1] < 2:
        return None
    numeric_df = numeric_df.iloc[:, :MAX_CORRELATION_COLUMNS]
    if len(numeric_df) < PRIVACY_MIN_ROWS:
        return None
    corr = numeric_df.corr().round(3)
    labels = corr.columns.tolist()
    matrix = [
        [round(float(corr.loc[row_label, col_label]), 3) for col_label in labels]
        for row_label in labels
    ]
    return {"labels": labels, "matrix": matrix}


def _observations_to_dataframe(observations):
    """
    Convert a queryset of Observation objects into a pandas DataFrame.
    Each attribute becomes a column, with rows representing unique observation contexts.
    
    Each row represents a unique combination of patient + time + location.
    This preserves all legitimate data variations (e.g., measurements at different times/locations).
    
    Handles missing relationships gracefully and is optimized for database efficiency.
    
    Args:
        observations: Queryset of Observation objects
        
    Returns:
        pandas DataFrame with columns for each attribute, plus _patient_id, _time_id, _location_id
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Early return for empty querysets
        if not observations.exists():
            logger.info("No observations to convert to DataFrame")
            return pd.DataFrame()
        
        # Check if queryset already has deferred fields (from .only() or .defer())
        # If so, we cannot add select_related() - it will cause a FieldError
        query = observations.query
        has_deferred_fields = bool(query.deferred_loading[0] or query.deferred_loading[1])
        
        if not has_deferred_fields:
            # Safe to optimize with select_related if no deferred fields
            observations = observations.select_related("patient", "attribute", "time", "location")
            logger.debug("Applied select_related optimization")
        else:
            logger.debug("Queryset has deferred fields, skipping select_related optimization")
        
        # Build a list of records where each record is a unique (patient, time, location) combination
        # Structure: rows = unique (patient, time, location), columns = attributes + context
        records = {}
        skipped_count = 0
        
        for obs in observations:
            try:
                # Safely get patient_id - handle None gracefully using FK ID
                patient_id = None
                if obs.patient_id:  # Check FK ID first (no extra query)
                    try:
                        # Patient should be pre-loaded via select_related
                        patient_id = obs.patient.unique_id if obs.patient else f"patient_{obs.patient_id}"
                    except (AttributeError, KeyError) as e:
                        # Fallback if patient relationship is broken or deferred
                        logger.warning("Could not access patient for observation %s: %s", obs.id, e)
                        patient_id = f"patient_{obs.patient_id}"
                
                # Safely get time_id and location_id using FK IDs directly
                time_id = obs.time_id
                location_id = obs.location_id
                
                # Skip observations without any identifying information
                # (per model validation, at least patient or location should exist)
                if patient_id is None and location_id is None:
                    skipped_count += 1
                    logger.debug(f"Skipping observation {obs.id}: no patient or location")
                    continue
                
                # Create a unique row identifier based on patient, time, and location
                row_key = (patient_id, time_id, location_id)
                
                # Initialize record if not exists
                if row_key not in records:
                    records[row_key] = {
                        '_patient_id': patient_id,
                        '_time_id': time_id,
                        '_location_id': location_id,
                    }
                
                # Safely get attribute name
                try:
                    attr_name = obs.attribute.variable_name
                except Exception as e:
                    logger.warning(f"Could not access attribute for observation {obs.id}: {e}")
                    skipped_count += 1
                    continue
                
                # Get the appropriate value based on type - priority order
                value = None
                if obs.float_value is not None:
                    value = obs.float_value
                elif obs.int_value is not None:
                    value = obs.int_value
                elif obs.text_value:
                    value = obs.text_value
                elif obs.boolean_value is not None:
                    value = obs.boolean_value
                elif obs.datetime_value is not None:
                    value = obs.datetime_value
                
                # If we already have a value for this attribute in this context, it's a duplicate
                # Keep the first one we encounter
                if attr_name not in records[row_key]:
                    records[row_key][attr_name] = value
                    
            except Exception as e:
                logger.warning(f"Error processing observation {getattr(obs, 'id', 'unknown')}: {e}")
                skipped_count += 1
                continue
        
        if skipped_count > 0:
            logger.info(f"Skipped {skipped_count} observations due to missing data or errors")
        
        # Convert to DataFrame
        if not records:
            logger.warning("No valid records created from observations")
            return pd.DataFrame()
        
        df = pd.DataFrame.from_dict(records, orient='index')
        
        # Reset index to make row numbers clean
        df = df.reset_index(drop=True)
        
        logger.info(f"Successfully created DataFrame with {len(df)} rows and {len(df.columns)} columns")
        
        return df
        
    except Exception as e:
        logger.error(f"Fatal error in _observations_to_dataframe: {e}", exc_info=True)
        # Return empty DataFrame rather than crashing
        return pd.DataFrame()


def _generate_eda_from_dataframe(
    df: pd.DataFrame,
    total_rows: int | None = None,
    total_columns: int | None = None,
    sanitize_pii: bool = True,
) -> dict[str, Any]:
    """
    Generate privacy-aware EDA statistics from a pandas DataFrame.
    This is the unified function used by both source and transformed data analysis.
    
    Args:
        df: DataFrame to analyze
        total_rows: Total row count (defaults to len(df))
        total_columns: Total column count (defaults to len(df.columns))
        sanitize_pii: Whether to filter out potential PII columns
    
    Returns:
        Dictionary containing EDA summary with statistics and visualizations
    """
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"_generate_eda_from_dataframe: {len(df)} rows, {len(df.columns)} cols, sanitize={sanitize_pii}")
    logger.info(f"Input columns: {list(df.columns)}")
    logger.info(f"Column dtypes: {dict(df.dtypes)}")
    
    response: dict[str, Any] = {
        "available": False,
        "reason": "",
        "summary": {},
        "numeric_columns": [],
        "categorical_columns": [],
        "string_columns": [],
        "correlation": None,
    }

    if df.empty:
        response["reason"] = "Dataset appears to be empty."
        return response

    total_rows = total_rows or len(df)
    total_columns = total_columns or len(df.columns)

    sanitised_columns: List[str] = []
    filtered_columns: List[str] = []
    for column in df.columns:
        column_lower = str(column).lower()
        if column_lower in PII_EXACT:
            sanitised_columns.append(column)
            continue
        if column_lower.endswith(PII_SUFFIXES):
            sanitised_columns.append(column)
            continue
        if any(keyword in column_lower for keyword in PII_KEYWORDS):
            sanitised_columns.append(column)
            continue
        filtered_columns.append(column)

    if sanitised_columns:
        df = df[filtered_columns]
    response["sanitised_columns"] = sanitised_columns

    response["summary"] = {
        "row_count": total_rows,
        "column_count": total_columns,
        "sampled_rows": int(len(df)),
        "missing_values": int(df.isna().sum().sum()),
        "privacy_threshold": PRIVACY_MIN_ROWS,
        "included_columns": len(df.columns),
        "excluded_columns": len(sanitised_columns),
    }

    if len(df) < PRIVACY_MIN_ROWS:
        response["reason"] = (
            "Only aggregated statistics are available because the sample contains fewer than "
            f"{PRIVACY_MIN_ROWS} rows."
        )
        response["available"] = True
        return response

    # Numeric features
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    for col in numeric_cols:
        original_series = df[col]
        series = _safe_numeric(original_series)
        if len(series) < PRIVACY_MIN_ROWS:
            continue

        q1 = series.quantile(0.25)
        median = series.quantile(0.5)
        q3 = series.quantile(0.75)
        p10 = series.quantile(0.10)
        p90 = series.quantile(0.90)
        
        # Generate histogram data
        histogram_data = _histogram_for(series)
        
        # Build stats dict for dashboard
        stats_dict = {
            "count": int(series.count()),
            "missing": int(original_series.isna().sum()),
            "mean": _format_number(series.mean()),
            "median": _format_number(median),
            "std": _format_number(series.std()),
            "variance": _format_number(series.var()),
            "skewness": _format_number(series.skew()),
            "min": _format_number(series.min()),
            "max": _format_number(series.max()),
            "p10": _format_number(p10),
            "p90": _format_number(p90),
            "boxplot": {
                "min": _format_number(series.min()),
                "p10": _format_number(p10),
                "q1": _format_number(q1),
                "median": _format_number(median),
                "q3": _format_number(q3),
                "p90": _format_number(p90),
                "max": _format_number(series.max()),
                "iqr": _format_number(q3 - q1),
            },
        }
        
        # Generate interactive Plotly dashboard
        dashboard_html = _generate_numeric_dashboard(series, col, stats_dict)
        
        stats_dict["name"] = col
        stats_dict["histogram"] = histogram_data
        stats_dict["dashboard_html"] = dashboard_html
        
        response["numeric_columns"].append(stats_dict)

    # Categorical & High-cardinality Text Handling
    categorical_cols = df.select_dtypes(include=["object", "category", "bool"]).columns.tolist()
    high_card_text_cols: List[str] = []

    for col in categorical_cols:
        series = df[col].astype(str) if df[col].dtype == bool else df[col]
        unique = int(series.nunique(dropna=True))
        # High-cardinality textual/ categorical column: skip categorical dashboard, defer to word cloud/text analysis
        is_textual = (df[col].dtype == object) or (str(df[col].dtype) == 'category')
        if is_textual and unique >= WORD_CLOUD_MIN_UNIQUE:
            logger.debug(
                "High-cardinality text column identified for word cloud: %s (unique=%s threshold=%s)",
                col,
                unique,
                WORD_CLOUD_MIN_UNIQUE,
            )
            high_card_text_cols.append(col)
            continue

        top_values = _categorical_summary(series)
        if not top_values:
            continue
        total = int(series.dropna().shape[0])
        accounted = sum(item["count"] for item in top_values)
        missing = int(df[col].isna().sum())
        
        # Extract data for dashboard
        labels = [item["value"] for item in top_values]
        counts = [item["count"] for item in top_values]
        percentages = [item["percentage"] for item in top_values]
        
        dashboard_html = _generate_categorical_dashboard(
            labels, counts, percentages, col, total, unique, missing
        )
        response["categorical_columns"].append(
            {
                "name": col,
                "unique": unique,
                "top_values": top_values,
                "other_count": max(total - accounted, 0),
                "missing": missing,
                "dashboard_html": dashboard_html,
            }
        )

    # Word cloud / text dashboards only for high-cardinality text columns
    if not high_card_text_cols:
        logger.debug(
            "No high-cardinality text columns met threshold=%s (examined=%s)",
            WORD_CLOUD_MIN_UNIQUE,
            len(categorical_cols),
        )

    if not WORDCLOUD_AVAILABLE:
        logger.debug("wordcloud library not available; skipping image generation")

    for col in high_card_text_cols:
        tokens = _tokenise_text(df[col])
        if not tokens:
            logger.debug(
                "No tokens produced for high-cardinality column %s (possibly all filtered or below privacy threshold)",
                col,
            )
            continue
        word_cloud_image = _generate_word_cloud_image(tokens)
        logger.debug(
            "Generated word cloud for %s: tokens=%d has_image=%s", col, len(tokens), bool(word_cloud_image)
        )
        dashboard_html = _generate_word_cloud_dashboard(tokens, col)
        response["string_columns"].append(
            {
                "name": col,
                "tokens": tokens,
                "word_cloud_image": word_cloud_image,
                "dashboard_html": dashboard_html,
                "high_cardinality": True,
                "unique_count": int(df[col].nunique(dropna=True)),
            }
        )

    response["summary"]["numeric_columns"] = len(response["numeric_columns"])
    response["summary"]["categorical_columns"] = len(response["categorical_columns"])
    response["summary"]["text_columns"] = len(response["string_columns"])

    response["correlation"] = _correlation_matrix(df)
    response["available"] = True
    return response


def generate_eda_summary(raw_data_file) -> dict[str, Any]:
    """
    Return privacy-aware exploratory stats for a raw data file.
    This reads from the uploaded file and processes it as source data.
    Includes caching to avoid regenerating on every page load.
    """
    from pathlib import Path
    from django.utils import timezone
    
    # Check if we have a valid cache
    if raw_data_file.eda_cache_source:
        # Cache is valid if the file hasn't been modified since cache generation
        cache_valid = (
            raw_data_file.eda_cache_source_generated_at and
            raw_data_file.updated_at <= raw_data_file.eda_cache_source_generated_at
        )
        if cache_valid:
            logger.info(
                "Using cached source EDA for file %s (cached at %s)",
                raw_data_file.id,
                raw_data_file.eda_cache_source_generated_at,
            )
            return raw_data_file.eda_cache_source
    
    logger.info("Generating fresh source EDA for file %s", raw_data_file.id)
    
    response: dict[str, Any] = {
        "available": False,
        "reason": "",
        "summary": {},
        "numeric_columns": [],
        "categorical_columns": [],
        "string_columns": [],
        "correlation": None,
    }

    file_path = Path(raw_data_file.file.path)
    file_format = (raw_data_file.file_format or "csv").lower()

    try:
        df = _read_dataframe(file_path, file_format)
    except Exception as exc:  # pragma: no cover - defensive fallback
        response["reason"] = f"Could not read dataset: {exc}"[:200]
        return response

    # Use actual DataFrame dimensions for accurate counts
    total_rows = len(df)
    total_columns = len(df.columns)
    
    # Use the unified function with PII sanitization
    result = _generate_eda_from_dataframe(
        df,
        total_rows=total_rows,
        total_columns=total_columns,
        sanitize_pii=True,
    )
    
    # Cache the result
    raw_data_file.eda_cache_source = result
    raw_data_file.eda_cache_source_generated_at = timezone.now()
    raw_data_file.save(update_fields=[
        "eda_cache_source",
        "eda_cache_source_generated_at",
    ])
    logger.info("Cached source EDA for file %s", raw_data_file.id)
    
    return result


def generate_eda_summary_from_observations(raw_data_file, study, is_transformed=False) -> dict[str, Any]:
    """
    Generate EDA summary from Observation objects (for ingested/transformed data).
    Includes caching to avoid regenerating on every page load.
    
    Args:
        raw_data_file: The RawDataFile object
        study: The Study whose observations to analyze (source or target study)
        is_transformed: Whether this is transformed data (affects time filtering)
        
    Returns:
        Dictionary containing EDA summary
    """
    from core.models import Observation
    from django.utils import timezone
    
    # Check cache for transformed data
    if is_transformed and raw_data_file.eda_cache_transformed:
        # Cache is valid if transformation hasn't changed
        cache_valid = (
            raw_data_file.eda_cache_transformed_generated_at and
            raw_data_file.transformed_at and
            raw_data_file.eda_cache_transformed_generated_at >= raw_data_file.transformed_at
        )
        if cache_valid:
            logger.info(
                "Using cached transformed EDA for file %s (cached at %s)",
                raw_data_file.id,
                raw_data_file.eda_cache_transformed_generated_at,
            )
            return raw_data_file.eda_cache_transformed
    
    logger.info(
        "Generating fresh %s data - File ID: %s, transformation_status: %s",
        "transformed" if is_transformed else "source",
        raw_data_file.id,
        raw_data_file.transformation_status if is_transformed else raw_data_file.processing_status,
    )
    
    response: dict[str, Any] = {
        "available": False,
        "reason": "",
        "summary": {},
        "numeric_columns": [],
        "categorical_columns": [],
        "string_columns": [],
        "correlation": None,
    }
    
    # Build the query to get relevant observations
    # Key insight: RawDataFile is linked to source study, which has patients
    # When ingested, observations are created for source study's patients
    # When transformed, new observations are created for target study, but SAME patients
    # So we filter by: source study patients + target study attributes

    if is_transformed:
        if raw_data_file.transformation_status not in [
            "completed",
            "in_progress",
        ]:
            response["reason"] = (
                f"Transformation has not been completed. "
                f"Current status: "
                f"{raw_data_file.get_transformation_status_display()}"
            )
            logger.warning(
                "Transformation not completed - status: %s",
                raw_data_file.transformation_status,
            )
            return response

        # Get the patients from the SOURCE study (linked to this raw data file)
        source_study = raw_data_file.study
        source_patients = Observation.objects.filter(
            attribute__studies=source_study
        ).values_list("patient_id", flat=True).distinct()

        patient_count = len(list(source_patients))
        logger.info(
            "Found %d unique patients in source study: %s (ID: %s)",
            patient_count,
            source_study.name,
            source_study.id,
        )

        # Now get transformed observations for target study, but ONLY for those patients
        query_filters = {
            "attribute__studies": study,  # target study
            "patient_id__in": source_patients,  # same patients as source
        }

        logger.info(
            "Querying transformed observations for target study: "
            "%s (ID: %s) filtered by %d patients from source study",
            study.name,
            study.id,
            patient_count,
        )
    else:
        # Source data should use the raw file, not observations
        response["reason"] = (
            "Source data should be analyzed from the raw file, "
            "not observations."
        )
        logger.error(
            "generate_eda_summary_from_observations called for source data - "
            "use generate_eda_summary instead"
        )
        return response
    
    # Optimize query by selecting only needed fields
    # Include FK IDs and related fields needed for DataFrame construction
    observations = Observation.objects.filter(**query_filters).select_related(
        "attribute",
        "patient",  # Need this for patient.unique_id
    ).only(
        # Observation fields
        "id",
        "patient_id",  # FK ID
        "time_id",     # FK ID
        "location_id", # FK ID
        "float_value",
        "int_value",
        "text_value",
        "boolean_value",
        "datetime_value",
        # Attribute fields (via select_related)
        "attribute__id",
        "attribute__variable_name",
        "attribute__variable_type",
        # Patient fields (via select_related)
        "patient__id",
        "patient__unique_id",  # Need this for DataFrame
    )
    obs_count = observations.count() #TO DO: need to fix this to only show unique observations, patient, location, time and an attirbute with a value
    
    logger.info(
        "Found %d observations for %s study (ID: %s) with filters: %s",
        obs_count,
        "transformed" if is_transformed else "source",
        study.id,
        query_filters,
    )
    
    if not observations.exists():
        data_type = "transformed" if is_transformed else "source"
        response["reason"] = f"No {data_type} observations found for this data file."
        logger.warning(
            "No observations found for %s data (study ID: %s)",
            data_type,
            study.id,
        )
        return response
    
    # Convert observations to DataFrame
    logger.info("Converting %d observations to DataFrame...", obs_count)
    df = _observations_to_dataframe(observations)
    
    if df.empty:
        response["reason"] = "Could not construct data table from observations."
        logger.error("DataFrame conversion resulted in empty dataframe")
        return response
    
    logger.info(
        "DataFrame created: %d rows, %d columns. Columns: %s",
        len(df),
        len(df.columns),
        list(df.columns),
    )
    
    # Use the unified function without PII sanitization (already sanitized at ingestion)
    eda_result = _generate_eda_from_dataframe(
        df,
        total_rows=len(df),
        total_columns=len(df.columns),
        sanitize_pii=False,  # Already sanitized
    )
    
    logger.info(
        "EDA result for %s data - numeric cols: %s, categorical cols: %s, string cols: %s",
        "transformed" if is_transformed else "source",
        [c['name'] for c in eda_result.get('numeric_columns', [])],
        [c['name'] for c in eda_result.get('categorical_columns', [])],
        [c['name'] for c in eda_result.get('string_columns', [])],
    )
    
    # Cache the result for transformed data
    if is_transformed:
        from django.utils import timezone
        raw_data_file.eda_cache_transformed = eda_result
        raw_data_file.eda_cache_transformed_generated_at = timezone.now()
        raw_data_file.save(update_fields=[
            "eda_cache_transformed",
            "eda_cache_transformed_generated_at",
        ])
        logger.info("Cached transformed EDA for file %s", raw_data_file.id)
    
    return eda_result

