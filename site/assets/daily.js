"use strict";
(() => {
  const KEY = "daily-curiosity-favorites-v1";
  let favorites = {};
  try { favorites = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch { favorites = {}; }
  if (!favorites || Array.isArray(favorites) || typeof favorites !== "object") favorites = {};
  const save = () => {
    try { localStorage.setItem(KEY, JSON.stringify(favorites)); return true; }
    catch { alert("Favorites could not be saved in this browser. Please check storage permissions."); return false; }
  };
  const cards = () => [...document.querySelectorAll("article.card")];
  const readStory = card => {
    try { return JSON.parse(card.dataset.story || "{}"); } catch { return {}; }
  };
  const safeLink = url => /^https?:\/\//i.test(url || "") ? url : "#";
  const sayText = story => [
    story.headline || story.title || "", story.brief || "",
    story.why ? "Why it matters. " + story.why : ""
  ].filter(Boolean).join(". ");
  const speechAvailable = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
  const audio = window.speechSynthesis;
  const speedSelect = document.getElementById("speech-speed");
  const speed = () => Number(speedSelect?.value || 1);
  let speaking = false;
  let activeStoryId = null;
  function setSpeechUI() {
    document.querySelectorAll("button.listen-story").forEach(button => {
      const story = readStory(button.closest(".card"));
      const current = speaking && activeStoryId && story.id === activeStoryId;
      button.textContent = current ? (audio?.paused ? "▶ Resume" : "⏸ Pause") : "🔊 Listen";
      button.setAttribute("aria-pressed", current ? "true" : "false");
      button.title = current ? (audio?.paused ? "Resume reading" : "Pause reading") : "Listen to this story";
    });
    const pause = document.getElementById("speech-pause");
    if (pause) { pause.textContent = audio?.paused ? "Resume" : "Pause"; pause.disabled = !speaking; }
    const stop = document.getElementById("speech-stop");
    if (stop) stop.disabled = !speaking;
  }
  function stopSpeech() {
    if (speechAvailable) audio.cancel();
    speaking = false;
    activeStoryId = null;
    setSpeechUI();
  }
  function speak(stories, storyId = null) {
    if (!speechAvailable) { alert("This browser does not support speech playback."); return; }
    const scripts = stories.map(sayText).filter(Boolean);
    if (!scripts.length) return;
    stopSpeech();
    speaking = true;
    activeStoryId = storyId;
    scripts.forEach((script, index) => {
      const utterance = new SpeechSynthesisUtterance(script);
      utterance.lang = "en-US";
      utterance.rate = speed();
      const voice = audio.getVoices().find(v => v.lang?.toLowerCase().startsWith("en") && v.localService);
      if (voice) utterance.voice = voice;
      if (index === scripts.length - 1) {
        utterance.onend = () => { speaking = false; activeStoryId = null; setSpeechUI(); };
        utterance.onerror = () => { speaking = false; activeStoryId = null; setSpeechUI(); };
      }
      audio.speak(utterance);
    });
    setSpeechUI();
  }
  function syncFavoriteButtons() {
    document.querySelectorAll("button.favorite").forEach(button => {
      const story = readStory(button.closest(".card"));
      const active = Boolean(favorites[story.id]);
      button.textContent = active ? "♥ Saved" : "♡ Save";
      button.setAttribute("aria-pressed", active ? "true" : "false");
      button.title = active ? "Remove from Favorites" : "Add to Favorites";
    });
  }
  function makeElement(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }
  function favoriteCard(story) {
    const card = makeElement("article", "card");
    card.dataset.story = JSON.stringify(story);
    const meta = makeElement("div", "meta");
    meta.append(makeElement("span", "", (story.icon || "📚") + " " + (story.topic || "Other")));
    meta.append(makeElement("span", "", [story.source, story.published].filter(Boolean).join(" · ")));
    card.append(meta);
    card.append(makeElement("h2", "", story.headline || story.title || ""));
    if (story.korean) {
      const ko = makeElement("p", "ko", story.korean);
      ko.lang = "ko"; card.append(ko);
    }
    card.append(makeElement("p", "", story.brief || ""));
    const why = makeElement("p", "why");
    why.append(makeElement("b", "", "Why it matters: "), document.createTextNode(story.why || ""));
    card.append(why);
    if (story.evidence_note) card.append(makeElement("p", "caution", story.evidence_note));
    const original = makeElement("a", "", "Read original ↗");
    original.href = safeLink(story.url); original.target = "_blank"; original.rel = "noopener noreferrer";
    card.append(original);
    const controls = makeElement("div", "story-actions");
    const fav = makeElement("button", "favorite", "♡ Save");
    fav.type = "button"; fav.setAttribute("aria-label", "Toggle favorite");
    const listen = makeElement("button", "listen-story", "🔊 Listen");
    listen.type = "button";
    controls.append(fav, listen);
    card.append(controls);
    return card;
  }
  function renderFavorites() {
    const container = document.getElementById("favorites-list");
    if (!container) return;
    container.replaceChildren();
    const entries = Object.values(favorites).filter(x => x && typeof x === "object")
      .sort((a, b) => (b.saved_at || "").localeCompare(a.saved_at || ""));
    const count = document.getElementById("favorites-count");
    if (count) count.textContent = entries.length + " saved stories";
    const empty = document.getElementById("favorites-empty");
    if (empty) empty.hidden = entries.length > 0;
    entries.forEach(story => container.append(favoriteCard(story)));
    syncFavoriteButtons();
  }
  document.addEventListener("click", event => {
    const favoriteButton = event.target.closest("button.favorite");
    if (favoriteButton) {
      const story = readStory(favoriteButton.closest(".card"));
      if (!story.id) return;
      if (favorites[story.id]) delete favorites[story.id];
      else favorites[story.id] = {...story, saved_at: new Date().toISOString()};
      save(); syncFavoriteButtons(); renderFavorites();
      return;
    }
    const listen = event.target.closest("button.listen-story");
    if (listen) {
      const story = readStory(listen.closest(".card"));
      if (speaking && activeStoryId === story.id) {
        if (audio.paused) audio.resume(); else audio.pause();
        setSpeechUI();
      } else {
        speak([story], story.id);
      }
    }
  });
  document.getElementById("listen-all")?.addEventListener("click", () => speak(cards().filter(c => !c.hidden).map(readStory)));
  document.getElementById("speech-stop")?.addEventListener("click", stopSpeech);
  document.getElementById("speech-pause")?.addEventListener("click", () => {
    if (!speechAvailable || !speaking) return;
    if (audio.paused) audio.resume(); else audio.pause();
    setSpeechUI();
  });
  if (!speechAvailable) document.querySelectorAll(".audio-controls, .listen-story").forEach(el => { el.hidden = true; });
  const search = document.getElementById("search");
  const period = document.getElementById("period");
  const sections = [...document.querySelectorAll(".issue")];
  function updateArchive() {
    if (!search || !period) return;
    const query = search.value.trim().toLocaleLowerCase();
    const range = period.value === "all" ? Infinity : Number(period.value);
    const now = new Date(), midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    let matches = 0;
    for (const section of sections) {
      let sectionMatches = 0;
      const dateParts = (section.dataset.date || "").split("-").map(Number);
      const issueDay = new Date(dateParts[0], dateParts[1] - 1, dateParts[2]);
      const ageDays = Math.floor((midnight.getTime() - issueDay.getTime()) / 86400000);
      const inRange = ageDays >= 0 && ageDays <= range;
      for (const card of section.querySelectorAll(".card")) {
        const hit = inRange && (!query || (card.dataset.search || "").includes(query));
        card.hidden = !hit;
        if (hit) { sectionMatches++; matches++; }
      }
      section.hidden = sectionMatches === 0;
    }
    const count = document.getElementById("count");
    if (count) count.textContent = matches + " matching stories";
    const empty = document.getElementById("empty");
    if (empty) empty.hidden = matches !== 0;
  }
  search?.addEventListener("input", updateArchive);
  period?.addEventListener("change", updateArchive);
  document.getElementById("clear-filters")?.addEventListener("click", () => {
    if (search) search.value = "";
    if (period) period.value = "all";
    updateArchive();
  });
  updateArchive();
  window.addEventListener("storage", event => {
    if (event.key !== KEY) return;
    try { favorites = JSON.parse(event.newValue || "{}") || {}; } catch { favorites = {}; }
    syncFavoriteButtons(); renderFavorites();
  });
  window.addEventListener("pagehide", stopSpeech);
  syncFavoriteButtons(); renderFavorites(); setSpeechUI();
})();
