// Open Source Radar: client-side filtering over data/issues.json. No build step, no dependencies.
(() => {
  "use strict";

  const PAGE_SIZE = 50;
  const CONTRIBUTING_PATHS = new Set([
    "CONTRIBUTING.md",
    ".github/CONTRIBUTING.md",
    "docs/CONTRIBUTING.md",
    "CONTRIBUTING.rst",
  ]);
  const BEGINNER_LABEL = 'label:"good first issue"';
  const form = document.getElementById("filters");
  const list = document.getElementById("issue-list");
  const template = document.getElementById("issue-template");
  const summary = document.getElementById("summary");
  const empty = document.getElementById("empty");
  const more = document.getElementById("more");
  const moreButton = document.getElementById("more-button");
  const queryText = document.getElementById("query-text");
  const queryLink = document.getElementById("query-link");
  const updated = document.getElementById("updated");
  const languageFeed = document.getElementById("language-feed");
  const languageFeedLink = document.getElementById("language-feed-link");
  const topicFeed = document.getElementById("topic-feed");
  const topicFeedLink = document.getElementById("topic-feed-link");

  let data = null;
  let filtered = [];
  let shown = 0;
  let previousQueryTerms = new Set();

  const numberFormat = new Intl.NumberFormat("en");
  const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

  // ---- Theme -------------------------------------------------------------------------------

  document.querySelector(".theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const current = root.dataset.theme || (systemDark ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("radar-theme", next); } catch (e) { /* storage unavailable */ }
  });

  // ---- Helpers -----------------------------------------------------------------------------

  function daysAgo(isoDate) {
    const days = Math.floor((Date.now() - new Date(isoDate + "T00:00:00Z").getTime()) / 86400000);
    if (days <= 0) return "updated today";
    if (days === 1) return "updated yesterday";
    if (days < 60) return `updated ${days} days ago`;
    return `updated ${Math.round(days / 30)} months ago`;
  }

  function readFilters() {
    const values = Object.fromEntries(new FormData(form).entries());
    values.q = (values.q || "").trim();
    return values;
  }

  function writeUrl(values) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(values)) {
      if (value && !(key === "sort" && value === "updated")) params.set(key, value);
    }
    const qs = params.toString();
    history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
  }

  function restoreFromUrl() {
    const params = new URLSearchParams(location.search);
    for (const element of form.elements) {
      if (element.name && params.has(element.name)) element.value = params.get(element.name);
    }
  }

  // ---- The query line ------------------------------------------------------------------------

  function githubQueryTerms(values) {
    const terms = ["is:issue", "is:open", "no:assignee", "-linked:pr"];
    if (values.level === "beginner") terms.push(BEGINNER_LABEL);
    if (values.level === "help-wanted") terms.push('label:"help wanted"');
    if (values.language) {
      const name = values.language.includes(" ") ? `"${values.language}"` : values.language;
      terms.push(`language:${name}`);
    }
    if (values.q) terms.push(values.q);
    return terms;
  }

  function renderQuery(values) {
    const terms = githubQueryTerms(values);
    const current = new Set(terms);
    queryText.replaceChildren();
    terms.forEach((term, index) => {
      if (index) queryText.append(" ");
      const span = document.createElement("span");
      const colon = term.indexOf(":");
      if (colon > 0 && !term.startsWith('"')) {
        const key = document.createElement("span");
        key.className = "q-key";
        key.textContent = term.slice(0, colon + 1);
        span.append(key, term.slice(colon + 1));
      } else {
        span.textContent = term;
      }
      if (previousQueryTerms.size && !previousQueryTerms.has(term)) span.classList.add("q-new");
      queryText.append(span);
    });
    previousQueryTerms = current;
    const url = new URL("https://github.com/search");
    url.searchParams.set("q", terms.join(" "));
    url.searchParams.set("type", "issues");
    url.searchParams.set("s", "updated");
    queryLink.href = url.toString();
  }

  // ---- Filtering -----------------------------------------------------------------------------

  function contributingPath(repo) {
    return CONTRIBUTING_PATHS.has(repo.contributing) ? repo.contributing : null;
  }

  function matchesRules(repo, rule) {
    const policy = repo.policy || {};
    if (rule === "contributing") return contributingPath(repo) !== null;
    if (rule === "no-ai-restriction") return policy.ai !== "restricted";
    if (rule === "no-cla") return !policy.cla;
    if (rule === "simple") return (policy.ai === "none" || !policy.ai) && !policy.cla && !policy.dco;
    return true;
  }

  function applyFilters() {
    const values = readFilters();
    const needle = values.q.toLowerCase();
    const repos = data.repositories;

    const activityDays = Number(values.activity) || 0;
    const activityCutoff = activityDays ? Date.now() - activityDays * 86400000 : null;

    filtered = data.issues.filter((issue) => {
      const repo = repos[issue.repo];
      if (values.language && repo.language !== values.language) return false;
      if (values.topic && !repo.buckets.includes(values.topic)) return false;
      if (values.level && issue.level !== values.level) return false;
      if (activityCutoff !== null && !(new Date(issue.updated + "T00:00:00Z").getTime() >= activityCutoff)) return false;
      if (values.rules && !matchesRules(repo, values.rules)) return false;
      if (needle) {
        const haystack = `${issue.title} ${issue.repo} ${issue.labels.join(" ")} ${repo.description}`.toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });

    const sorters = {
      updated: (a, b) => b.updated.localeCompare(a.updated) || repos[b.repo].stars - repos[a.repo].stars,
      new: (a, b) => b.created.localeCompare(a.created),
      stars: (a, b) => repos[b.repo].stars - repos[a.repo].stars || b.updated.localeCompare(a.updated),
      quiet: (a, b) => a.comments - b.comments || b.updated.localeCompare(a.updated),
    };
    filtered.sort(sorters[values.sort] || sorters.updated);

    writeUrl(values);
    renderQuery(values);
    list.replaceChildren();
    shown = 0;
    renderMore();

    const projectCount = new Set(filtered.map((issue) => issue.repo)).size;
    summary.innerHTML = filtered.length
      ? `<strong>${numberFormat.format(filtered.length)}</strong> issues in <strong>${numberFormat.format(projectCount)}</strong> projects match.`
      : "No issues match these filters.";
    const languageSlug = data.language_slugs?.[values.language];
    languageFeed.hidden = !languageSlug;
    if (languageSlug) {
      languageFeedLink.href = `feeds/${languageSlug}.xml`;
      languageFeedLink.textContent = `RSS feed for ${values.language} beginner issues`;
    }

    const hasTopicFeed = data.topic_feed_slugs?.includes(values.topic);
    topicFeed.hidden = !hasTopicFeed;

    if (hasTopicFeed) {
      topicFeedLink.href = `feeds/topics/${values.topic}.xml`;
      const topicTitle = data.topic_titles?.[values.topic];
      topicFeedLink.textContent = `RSS feed for ${topicTitle} beginner issues`;
    }
    empty.hidden = filtered.length > 0;
  }

  // ---- Rendering -----------------------------------------------------------------------------

  function badge(listElement, text, href, className) {
    const item = document.createElement("li");
    if (className) item.className = className;
    const inner = document.createElement(href ? "a" : "span");
    inner.textContent = text;
    if (href) {
      inner.href = href;
      inner.target = "_blank";
      inner.rel = "noopener";
    }
    item.append(inner);
    listElement.append(item);
  }

  function renderIssue(issue) {
    const repo = data.repositories[issue.repo];
    const node = template.content.firstElementChild.cloneNode(true);

    const title = node.querySelector(".issue-title");
    title.href = issue.url;
    title.textContent = issue.title;
    const num = document.createElement("span");
    num.className = "num";
    num.textContent = ` #${issue.number}`;
    title.append(num);

    const repoLink = node.querySelector(".repo");
    repoLink.href = repo.url;
    repoLink.textContent = issue.repo;
    node.querySelector(".stars").textContent = `${compact.format(repo.stars)} stars`;
    node.querySelector(".lang").textContent = repo.language || "";
    node.querySelector(".updated").textContent = daysAgo(issue.updated);
    node.querySelector(".repo-desc").textContent = repo.description || "";

    const level = node.querySelector(".level");
    level.classList.add(issue.level);
    level.textContent = issue.level === "beginner" ? "Beginner label" : "Help wanted";

    const comments = node.querySelector(".comments");
    comments.textContent = issue.comments === 1 ? "1 comment" : `${issue.comments} comments`;
    if (issue.maintainer_replied === true) {
      const reply = document.createElement("span");
      reply.className = "comments";
      reply.textContent = "Maintainer replied";
      reply.title = "The latest fetched comment is from a repository owner, member or collaborator.";
      comments.after(reply);
    }

    const badges = node.querySelector(".badges");
    const policy = repo.policy || {};
    const fileUrl = (path) => `${repo.url}/blob/HEAD/${path}`;
    if (policy.ai === "restricted") badge(badges, "AI restricted", fileUrl(policy.ai_file), "ai-restricted");
    if (policy.ai === "disclose") badge(badges, "Disclose AI use", fileUrl(policy.ai_file));
    if (policy.ai === "policy") badge(badges, "AI policy", fileUrl(policy.ai_file));
    if (policy.cla) badge(badges, "CLA");
    if (policy.dco) badge(badges, "DCO sign-off");
    const guide = contributingPath(repo);
    if (guide) badge(badges, "Contributing guide", fileUrl(guide));
    if (issue.closed_pr) badge(badges, "Earlier PR closed", issue.url);
    if (!badges.children.length) badges.remove();

    return node;
  }

  function renderMore() {
    const fragment = document.createDocumentFragment();
    const next = filtered.slice(shown, shown + PAGE_SIZE);
    next.forEach((issue) => fragment.append(renderIssue(issue)));
    list.append(fragment);
    shown += next.length;
    more.hidden = shown >= filtered.length;
    moreButton.textContent = `Show ${Math.min(PAGE_SIZE, filtered.length - shown)} more of ${numberFormat.format(filtered.length - shown)}`;
  }

  function populateSelect(select, options) {
    for (const [value, label] of options) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.append(option);
    }
  }

  // ---- Boot ----------------------------------------------------------------------------------

  async function boot() {
    try {
      const response = await fetch("data/issues.json", { cache: "no-cache" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      data = await response.json();
    } catch (error) {
      summary.textContent = "The issue list could not be loaded. Reload the page, or browse the lists on GitHub.";
      return;
    }

    const repos = data.repositories;
    const languageCounts = new Map();
    const topicCounts = new Map();
    for (const issue of data.issues) {
      const repo = repos[issue.repo];
      if (repo.language) languageCounts.set(repo.language, (languageCounts.get(repo.language) || 0) + 1);
      for (const bucket of repo.buckets) topicCounts.set(bucket, (topicCounts.get(bucket) || 0) + 1);
    }
    const titles = data.topic_titles || {};
    populateSelect(
      form.elements.language,
      [...languageCounts].sort((a, b) => b[1] - a[1]).map(([name, count]) => [name, `${name} (${count})`]),
    );
    populateSelect(
      form.elements.topic,
      [...topicCounts].sort((a, b) => b[1] - a[1]).map(([slug, count]) => [slug, `${titles[slug] || slug} (${count})`]),
    );

    const when = new Date(data.generated_at);
    updated.textContent = `Issues collected from the GitHub API on ${when.toLocaleString("en", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: "UTC",
    })} UTC. The list refreshes every 12 hours.`;

    restoreFromUrl();
    previousQueryTerms = new Set();
    applyFilters();

    let timer = null;
    form.addEventListener("input", (event) => {
      clearTimeout(timer);
      timer = setTimeout(applyFilters, event.target.name === "q" ? 180 : 0);
    });
    form.addEventListener("submit", (event) => event.preventDefault());
    form.addEventListener("reset", () => setTimeout(applyFilters, 0));

    document.addEventListener("keydown", (event) => {
      if (
        event.key === "/" &&
        !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)
      ) {
        event.preventDefault();
        form.elements.q.focus();
      }
    });

    document.getElementById("empty-reset").addEventListener("click", () => {
      form.reset();
      applyFilters();
    });
    moreButton.addEventListener("click", () => {
      const firstNew = shown;
      renderMore();
      const link = list.children[firstNew]?.querySelector(".issue-title");
      if (link) link.focus();
    });
  }

  boot();
})();
