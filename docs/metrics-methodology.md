# 📐 Analytical Metrics & Mathematical Methodology

EchoScope AI replaces naive creator vanity metrics with rigorous, transparent, and mathematically defensible indicators.

---

## 1. Central Tendency & Dispersion

### Why Median Over Mean?
On platforms like YouTube, video view distributions exhibit extreme positive skewness (power-law / Pareto distributions) driven by viral outliers. An arithmetic mean ($$\bar{x}$$) overstates the performance a creator can reliably expect.

$$\text{Median Views} = \tilde{x} = \text{middle element of sorted views } [v_1, v_2, \dots, v_n]$$

- **Sample Size ($$n$$):** Every median is reported alongside its exact video count ($$n$$) to qualify statistical significance.
- **Interquartile Range ($$P_{25}$$ and $$P_{75}$$):** Measures baseline consistency versus ceiling potential without assuming normality.

---

## 2. Views-to-Subscriber Ratio (VSR)

$$VSR = \frac{\sum_{i=1}^n \text{Views}_i}{\text{Subscribers}}$$

- **Purpose:** Indicates whether channel viewership is driven primarily by subscriber loyalty ($$VSR \approx 0.1 - 0.5$$) or by non-subscriber algorithmic discovery ($$VSR > 1.0$$).
- **Hidden Subscriber Policy:** If a channel owner conceals their subscriber count on YouTube, public API returns `NULL`. EchoScope preserves `NULL` and displays `"Hidden"`. It **never** artificially substitutes `0` or `1`, which would corrupt division and produce misleading ratios.

---

## 3. Sample-Consistent Likes-to-Views Ratio

$$\text{Likes-to-Views Ratio (\%)} = \left( \frac{\sum_{i \in K} \text{Likes}_i}{\sum_{i \in K} \text{Views}_i} \right) \times 100$$

Where $$K$$ represents the subset of videos whose like count is publicly visible ($$\text{Likes}_i \neq \text{NULL}$$).

- **Denominator Integrity:** If a video conceals like counts, its views are excluded from the denominator. This prevents artificially depressed engagement percentages.
- **Sample Tracking:** Reported with `likes_sample_size_videos = |K|`.

---

## 4. Views per Day (VPD)

$$\text{VPD}_i = \frac{\text{Views}_i}{\max(1, \text{Days Elapsed})}$$

$$\text{Days Elapsed} = \text{Collected At (or Period End)} - \text{Video Publication Date}$$

- **Snapshot Invariance:** VPD is calculated against the run's snapshot timestamp rather than local machine clock, ensuring historical runs reproduce identical numbers years later.
- **Reporting:** Both arithmetic mean VPD and median VPD are derived to differentiate baseline trajectory from viral spikes.

---

## 5. Share of Tracked Channel Views

$$\text{Share of Tracked Views (\%)} = \left( \frac{\text{Total Views of Channel}_j}{\sum_{k=1}^M \text{Total Views of Channel}_k} \right) \times 100$$

- **Semantic Honesty:** Explicitly labeled as **"Share of Tracked Channel Views"** rather than "Market Share". Public YouTube Data API only returns data for inspected channels; it is statistically dishonest to claim total platform niche ownership from an isolated sample.

---

## 6. Growth Signals (Inter-Snapshot Analysis)

When comparing channel snapshots across two runs ($$T_0$$ and $$T_1$$ with $$\Delta t = \text{days between runs}$$):

### 1. Comparable Video Set ($$C = V_{T0} \cap V_{T1}$$)
$$\Delta V_i = V_{T1, i} - V_{T0, i}$$

$$\text{Net Growth Views} = \sum_{i \in C} \Delta V_i$$

$$\text{Daily Growth Velocity } R = \frac{\text{Net Growth Views}}{\Delta t}$$

### 2. Anomaly Separation: Fraud Sweeps vs Growth
YouTube regularly audits and purges illegitimate or bot views:
- **Gross Positive Growth:** $$\sum_{i \in C, \Delta V_i > 0} \Delta V_i$$
- **Negative Adjustments (Audits):** $$\sum_{i \in C, \Delta V_i < 0} |\Delta V_i|$$

### 3. Upload Boundary Isolation
Videos present in $$T_1$$ but absent from $$T_0$$ are classified based on publication date:
- **True New Uploads:** $$\text{Published Date} > T_0$$
- **Unmatched Historical Videos:** $$\text{Published Date} \le T_0$$ (videos omitted from the older collection period, prevented from contaminating growth signals).
