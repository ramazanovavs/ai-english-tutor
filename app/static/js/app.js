const sb = supabase.createClient(APP_CONFIG.supabaseUrl, APP_CONFIG.supabaseAnonKey);
let currentSession = null;
let currentProfile = null;
let currentTest = null;
let mediaRecorder = null;
let mediaChunks = [];
let recordingStream = null;
let recordingStopTimer = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  }[c]));
}

async function api(path, options = {}) {
  if (!currentSession) throw new Error("Please sign in.");
  const headers = {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${currentSession.access_token}`,
    ...(options.headers || {})
  };
  const res = await fetch(path, {...options, headers});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Request failed");
  return data;
}


async function apiForm(path, formData) {
  if (!currentSession) throw new Error("Please sign in.");
  const res = await fetch(path, {
    method: "POST",
    headers: {"Authorization": `Bearer ${currentSession.access_token}`},
    body: formData
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Request failed");
  return data;
}

async function speakText(text, button = null) {
  if (!text || !currentSession) return;
  const oldLabel = button ? button.textContent : null;
  if (button) {
    button.disabled = true;
    button.textContent = "Generating audio…";
  }
  try {
    const res = await fetch("/api/audio/tts", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${currentSession.access_token}`
      },
      body: JSON.stringify({text})
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Could not create speech.");
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.onended = () => URL.revokeObjectURL(url);
    await audio.play();
  } catch (e) {
    alert(e.message);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldLabel;
    }
  }
}

async function signUp() {
  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;
  const { data, error } = await sb.auth.signUp({email, password});
  const el = document.getElementById("authMsg");
  if (error) return el.innerHTML = `<span class="text-danger">${esc(error.message)}</span>`;
  el.innerHTML = data.session
    ? `<span class="text-success">Account created.</span>`
    : `<span class="text-success">Account created. Check your email to confirm it.</span>`;
}

async function signIn() {
  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;
  const { data, error } = await sb.auth.signInWithPassword({email, password});
  if (error) {
    document.getElementById("authMsg").innerHTML = `<span class="text-danger">${esc(error.message)}</span>`;
    return;
  }
  currentSession = data.session;
  await showApp();
}

async function signOut() {
  await sb.auth.signOut();
  currentSession = null;
  document.getElementById("appView").classList.add("d-none");
  document.getElementById("authView").classList.remove("d-none");
}

async function showApp() {
  document.getElementById("authView").classList.add("d-none");
  document.getElementById("appView").classList.remove("d-none");
  await loadProfile();
}

async function loadProfile() {
  try {
    currentProfile = await api("/api/me");
    renderDashboard();
  } catch (e) {
    alert(e.message);
  }
}

function renderDashboard() {
  const profile = currentProfile.profile || {};
  const skills = currentProfile.skills || [];
  document.getElementById("navLevel").textContent = profile.cefr_level || "B1";

  document.getElementById("skillCards").innerHTML = skills.map(s => `
    <div class="col-md-6 col-xl-4">
      <div class="card-soft h-100">
        <div class="text-secondary small">${esc(s.skill)}</div>
        <div class="skill-score">${esc(s.score)}%</div>
        <div class="d-flex justify-content-between">
          <span class="badge text-bg-light">${esc(s.level)}</span>
          <span class="small text-secondary">${esc(s.attempts)} attempts</span>
        </div>
      </div>
    </div>
  `).join("");

  const weakest = [...skills].sort((a,b)=>a.score-b.score)[0];
  document.getElementById("recommendation").innerHTML = weakest
    ? `Your lowest tracked area is <strong>${esc(weakest.skill)}</strong> (${esc(weakest.score)}%). Try a short practice there next.`
    : "Start with a short diagnostic activity.";

  document.getElementById("progressTable").innerHTML = `
    <div class="table-responsive"><table class="table align-middle">
      <thead><tr><th>Skill</th><th>Score</th><th>CEFR</th><th>Attempts</th></tr></thead>
      <tbody>${skills.map(s => `<tr><td>${esc(s.skill)}</td><td>${esc(s.score)}%</td><td>${esc(s.level)}</td><td>${esc(s.attempts)}</td></tr>`).join("")}</tbody>
    </table></div>`;

  document.getElementById("recentActivity").innerHTML = (currentProfile.recent || []).length
    ? currentProfile.recent.map(r => `<div class="py-2 border-bottom"><strong>${esc(r.activity_type)}</strong> · ${esc(r.topic)} ${r.score != null ? `— ${esc(r.score)}%` : ""}</div>`).join("")
    : `<div class="text-secondary">No activities yet.</div>`;
}

function openPage(name) {
  document.querySelectorAll(".page").forEach(x => x.classList.add("d-none"));
  document.getElementById(`page-${name}`).classList.remove("d-none");
  document.querySelectorAll(".menu-btn").forEach(x => x.classList.toggle("active", x.dataset.page === name));
  if (name === "progress" || name === "dashboard") loadProfile();
}

document.querySelectorAll(".menu-btn").forEach(btn => {
  btn.addEventListener("click", () => openPage(btn.dataset.page));
});

function loading(elId, text="Working…") {
  document.getElementById(elId).innerHTML = `<div class="loader">${esc(text)}</div>`;
}

async function sendChat() {
  const input = document.getElementById("chatInput");
  const text = input.value.trim();
  if (!text) return;
  const box = document.getElementById("chatBox");
  box.innerHTML += `<div class="msg user">${esc(text)}</div>`;
  input.value = "";
  box.innerHTML += `<div id="typing" class="msg ai loader">Tutor is thinking…</div>`;
  box.scrollTop = box.scrollHeight;
  try {
    const data = await api("/api/chat", {method:"POST", body:JSON.stringify({text})});
    document.getElementById("typing").remove();
    box.innerHTML += `<div class="msg ai"><div>${esc(data.answer)}</div><button class="btn btn-sm btn-link px-0 mt-1" onclick="speakText(this.previousElementSibling.innerText, this)">🔊 Listen</button></div>`;
    box.scrollTop = box.scrollHeight;
  } catch(e) {
    document.getElementById("typing").textContent = e.message;
  }
}

async function checkGrammar() {
  const text = document.getElementById("grammarText").value.trim();
  if (!text) return;
  loading("grammarResult", "Analyzing grammar…");
  try {
    const d = await api("/api/grammar", {method:"POST", body:JSON.stringify({text})});
    document.getElementById("grammarResult").innerHTML = `
      <h4>${esc(d.score)}% · ${esc(d.topic)}</h4>
      <p>${esc(d.summary)}</p>
      <div class="alert alert-light"><strong>Corrected:</strong> ${esc(d.corrected_text)}</div>
      <h6>Errors</h6>
      ${(d.errors || []).map(x => `<div class="mb-2"><code>${esc(x.original)}</code> → <strong>${esc(x.correction)}</strong><br><span class="small">${esc(x.rule)}: ${esc(x.explanation)}</span></div>`).join("") || "<p>No major errors.</p>"}
      <h6 class="mt-3">Mini-practice</h6>
      <ol>${(d.practice || []).map(x => `<li>${esc(x)}</li>`).join("")}</ol>`;
    await loadProfile();
  } catch(e) { document.getElementById("grammarResult").textContent = e.message; }
}

async function makeVocab() {
  const topic = document.getElementById("vocabTopic").value.trim();
  const level = document.getElementById("vocabLevel").value;
  if (!topic) return;
  loading("vocabResult", "Building vocabulary set…");
  try {
    const d = await api("/api/vocabulary", {method:"POST", body:JSON.stringify({topic, level})});
    document.getElementById("vocabResult").innerHTML = `
      <h4>${esc(d.topic)}</h4>
      ${(d.items || []).map(x => `<div class="vocab-item"><strong>${esc(x.word)}</strong> <span class="text-secondary">${esc(x.part_of_speech)}</span><br>${esc(x.definition)}<br><em>${esc(x.example)}</em><br><small>Collocation: ${esc(x.collocation)}</small></div>`).join("")}
      <div class="alert alert-light mt-3">${esc(d.tip)}</div>`;
  } catch(e) { document.getElementById("vocabResult").textContent = e.message; }
}

async function checkWriting() {
  const text = document.getElementById("writingText").value.trim();
  const purpose = document.getElementById("writingPurpose").value;
  if (!text) return;
  loading("writingResult", "Reviewing writing…");
  try {
    const d = await api("/api/writing", {method:"POST", body:JSON.stringify({text, purpose})});
    document.getElementById("writingResult").innerHTML = `
      <h4>${esc(d.score)}% · approx. ${esc(d.estimated_level)}</h4>
      <h6>Strengths</h6><ul>${(d.strengths || []).map(x=>`<li>${esc(x)}</li>`).join("")}</ul>
      <h6>Improve</h6>${(d.improvements || []).map(x=>`<div class="mb-2"><strong>${esc(x.category)}:</strong> ${esc(x.issue)}<br><span class="text-secondary">${esc(x.suggestion)}</span></div>`).join("")}
      <h6 class="mt-3">Revised sample</h6><div class="alert alert-light">${esc(d.revised_sample)}</div>
      <strong>Next task:</strong> ${esc(d.next_task)}`;
    await loadProfile();
  } catch(e) { document.getElementById("writingResult").textContent = e.message; }
}

async function startSpeech() {
  const btn = document.getElementById("micBtn");

  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    return;
  }

  if (!navigator.mediaDevices || !window.MediaRecorder) {
    alert("Audio recording is not supported in this browser. You can type a transcript instead.");
    return;
  }

  try {
    recordingStream = await navigator.mediaDevices.getUserMedia({audio: true});
    const preferred = "audio/webm;codecs=opus";
    const options = MediaRecorder.isTypeSupported(preferred) ? {mimeType: preferred} : {};
    mediaRecorder = new MediaRecorder(recordingStream, options);
    mediaChunks = [];

    mediaRecorder.ondataavailable = event => {
      if (event.data && event.data.size > 0) mediaChunks.push(event.data);
    };

    mediaRecorder.onstop = async () => {
      clearTimeout(recordingStopTimer);
      btn.disabled = true;
      btn.textContent = "Transcribing…";
      if (recordingStream) recordingStream.getTracks().forEach(track => track.stop());

      try {
        const mimeType = mediaRecorder.mimeType || "audio/webm";
        const blob = new Blob(mediaChunks, {type: mimeType});
        const ext = mimeType.includes("ogg") ? "ogg" : mimeType.includes("mp4") ? "m4a" : "webm";
        const form = new FormData();
        form.append("audio", blob, `recording.${ext}`);
        form.append("scenario", document.getElementById("scenario").value);
        const data = await apiForm("/api/audio/transcribe", form);
        document.getElementById("speakingText").value = data.transcript;
      } catch (e) {
        alert(e.message);
      } finally {
        btn.disabled = false;
        btn.textContent = "🎙 Record answer";
        mediaRecorder = null;
        recordingStream = null;
        mediaChunks = [];
      }
    };

    mediaRecorder.start();
    btn.textContent = "⏹ Stop recording";
    recordingStopTimer = setTimeout(() => {
      if (mediaRecorder && mediaRecorder.state === "recording") mediaRecorder.stop();
    }, 120000);
  } catch (e) {
    alert("Microphone access failed. Allow microphone permission or type the transcript.");
    btn.textContent = "🎙 Record answer";
  }
}

async function checkSpeaking() {
  const text = document.getElementById("speakingText").value.trim();
  const scenario = document.getElementById("scenario").value;
  if (!text) return;
  loading("speakingResult", "Preparing speaking feedback…");
  try {
    const d = await api("/api/speaking", {method:"POST", body:JSON.stringify({text, scenario})});
    document.getElementById("speakingResult").innerHTML = `<pre class="feedback">${esc(d.feedback)}</pre><button class="btn btn-sm btn-outline-primary mt-2" onclick="speakText(this.previousElementSibling.innerText, this)">🔊 Listen to feedback</button>`;
  } catch(e) { document.getElementById("speakingResult").textContent = e.message; }
}

async function generateTest() {
  const level = document.getElementById("testLevel").value;
  const focus = document.getElementById("testFocus").value;
  document.getElementById("testResult").innerHTML = `<div class="loader">Generating test…</div>`;
  try {
    currentTest = await api("/api/test/generate", {method:"POST", body:JSON.stringify({level, focus})});
    renderTest();
  } catch(e) { document.getElementById("testResult").textContent = e.message; }
}

function renderTest() {
  document.getElementById("testResult").innerHTML = `
    <h4>${esc(currentTest.title)}</h4>
    <form id="testForm">
      ${(currentTest.questions || []).map(q => `
        <div class="card-soft my-3">
          <div class="fw-semibold mb-2">${esc(q.id)}. ${esc(q.question)}</div>
          ${(q.options || []).map((o,i)=>`
            <div class="form-check">
              <input class="form-check-input" type="radio" name="q${q.id}" value="${i}" id="q${q.id}_${i}">
              <label class="form-check-label" for="q${q.id}_${i}">${esc(o)}</label>
            </div>`).join("")}
        </div>`).join("")}
      <button type="button" class="btn btn-primary" onclick="submitTest()">Submit test</button>
    </form>`;
}

async function submitTest() {
  if (!currentTest) return;
  const answers = {};
  currentTest.questions.forEach(q => {
    const checked = document.querySelector(`input[name="q${q.id}"]:checked`);
    if (checked) answers[String(q.id)] = Number(checked.value);
  });
  try {
    const d = await api("/api/test/submit", {
      method:"POST",
      body:JSON.stringify({
        title: currentTest.title,
        level: currentTest.level,
        questions: currentTest.questions,
        answers
      })
    });
    document.getElementById("testResult").innerHTML += `
      <div class="alert alert-primary mt-3"><strong>Score: ${esc(d.score)}%</strong> (${esc(d.correct)}/${esc(d.total)})</div>
      ${(d.details || []).map(x => `<div class="small py-1">${x.correct ? "✅" : "❌"} Q${esc(x.id)} — ${esc(x.explanation)}</div>`).join("")}`;
    await loadProfile();
  } catch(e) { alert(e.message); }
}

(async () => {
  const { data } = await sb.auth.getSession();
  currentSession = data.session;
  if (currentSession) await showApp();
})();

// ========================= IELTS PREPARATION =========================
let currentIELTSWriting = null;
let currentIELTSSpeaking = null;
let currentIELTSReading = null;
let currentIELTSListening = null;
let ieltsRecorder = null;
let ieltsChunks = [];
let ieltsStream = null;

function bandCard(label, value) {
  const shown = value == null ? "—" : Number(value).toFixed(1);
  return `<div class="col-6 col-lg"><div class="card-soft text-center h-100"><div class="small text-secondary">${esc(label)}</div><div class="skill-score">${esc(shown)}</div><div class="small">Band</div></div></div>`;
}

async function loadIELTSDashboard() {
  try {
    const d = await api("/api/ielts/dashboard");
    const p = d.profile || {};
    document.getElementById("ieltsExamType").value = p.exam_type || "Academic";
    document.getElementById("ieltsTargetBand").value = String(p.target_band || 6.5);
    document.getElementById("ieltsExamDate").value = p.planned_exam_date || "";
    document.getElementById("ieltsBandCards").innerHTML = [
      bandCard("Overall", p.current_overall_band), bandCard("Listening", p.listening_band),
      bandCard("Reading", p.reading_band), bandCard("Writing", p.writing_band), bandCard("Speaking*", p.speaking_band)
    ].join("");
    document.getElementById("ieltsRecommendation").textContent = d.recommendation || "Complete a practice activity to get a recommendation.";
    const recent = [
      ...(d.objective_recent || []).map(x => ({name:`${x.section} · ${x.title}`, band:x.estimated_band, date:x.created_at})),
      ...(d.writing_recent || []).map(x => ({name:`Writing Task ${x.task_number}`, band:x.estimated_band, date:x.created_at})),
      ...(d.speaking_recent || []).map(x => ({name:`Speaking Part ${x.part}`, band:x.provisional_language_band, date:x.created_at}))
    ].sort((a,b)=>String(b.date).localeCompare(String(a.date))).slice(0,10);
    document.getElementById("ieltsRecent").innerHTML = recent.length
      ? recent.map(x=>`<div class="py-2 border-bottom d-flex justify-content-between"><span>${esc(x.name)}</span><strong>${x.band == null ? "—" : esc(Number(x.band).toFixed(1))}</strong></div>`).join("")
      : `<div class="text-secondary">No IELTS practice yet.</div>`;
  } catch(e) {
    document.getElementById("ieltsBandCards").innerHTML = `<div class="text-danger">${esc(e.message)}</div>`;
  }
}

async function saveIELTSProfile() {
  const payload = {
    exam_type: document.getElementById("ieltsExamType").value,
    target_band: Number(document.getElementById("ieltsTargetBand").value),
    planned_exam_date: document.getElementById("ieltsExamDate").value || null
  };
  try {
    await api("/api/ielts/profile", {method:"POST", body:JSON.stringify(payload)});
    await loadIELTSDashboard();
  } catch(e) { alert(e.message); }
}

async function generateIELTSWriting() {
  loading("ieltsWritingResult", "Generating IELTS writing task…");
  document.getElementById("ieltsWritingResult").innerHTML = "";
  const payload = {
    test_type: document.getElementById("ieltsWritingType").value,
    task_number: Number(document.getElementById("ieltsWritingTask").value),
    target_band: Number(document.getElementById("ieltsWritingTarget").value)
  };
  try {
    currentIELTSWriting = await api("/api/ielts/writing/generate", {method:"POST", body:JSON.stringify(payload)});
    document.getElementById("ieltsWritingPromptBox").classList.remove("d-none");
    document.getElementById("ieltsWritingTitle").textContent = currentIELTSWriting.title || "IELTS Writing";
    document.getElementById("ieltsWritingPrompt").textContent = currentIELTSWriting.prompt || "";
    document.getElementById("ieltsWritingMeta").textContent = `Minimum ${currentIELTSWriting.minimum_words || (payload.task_number===1?150:250)} words · Suggested ${currentIELTSWriting.suggested_minutes || (payload.task_number===1?20:40)} minutes`;
    document.getElementById("ieltsWritingResult").innerHTML = "";
  } catch(e) { document.getElementById("ieltsWritingResult").textContent = e.message; }
}

async function assessIELTSWriting() {
  const response = document.getElementById("ieltsWritingResponse").value.trim();
  const prompt = currentIELTSWriting?.prompt || document.getElementById("ieltsWritingPrompt").innerText.trim();
  if (!prompt) return alert("Generate a task first.");
  if (!response) return alert("Write your response first.");
  loading("ieltsWritingResult", "Assessing IELTS writing…");
  const task = Number(document.getElementById("ieltsWritingTask").value);
  try {
    const d = await api("/api/ielts/writing/assess", {method:"POST", body:JSON.stringify({
      test_type: document.getElementById("ieltsWritingType").value,
      task_number: task, prompt, response
    })});
    const taskKey = task === 1 ? "task_achievement" : "task_response";
    document.getElementById("ieltsWritingResult").innerHTML = `
      <div class="d-flex justify-content-between align-items-center"><h4>Estimated band ${esc(Number(d.overall_band).toFixed(1))}</h4><span class="badge text-bg-warning">Practice estimate</span></div>
      <div class="row g-2 my-2">
        ${bandCard(task===1?"Task Achievement":"Task Response", d[taskKey])}
        ${bandCard("Coherence & Cohesion", d.coherence_cohesion)}
        ${bandCard("Lexical Resource", d.lexical_resource)}
        ${bandCard("Grammar", d.grammatical_range_accuracy)}
      </div>
      <p class="small text-secondary">${esc(d.word_count)} words · ${esc(d.disclaimer)}</p>
      <h6>Strengths</h6><ul>${(d.strengths||[]).map(x=>`<li>${esc(x)}</li>`).join("")}</ul>
      <h6>How to improve</h6>${(d.improvements||[]).map(x=>`<div class="mb-2"><strong>${esc(x.criterion)}:</strong> ${esc(x.issue)}<br><span class="text-secondary">Action: ${esc(x.action)}</span></div>`).join("")}
      <h6 class="mt-3">Language corrections</h6>${(d.error_examples||[]).map(x=>`<div class="mb-2"><code>${esc(x.original)}</code> → <strong>${esc(x.correction)}</strong><br><small>${esc(x.explanation)}</small></div>`).join("") || '<p class="text-secondary">No major examples returned.</p>'}
      <div class="alert alert-light mt-3"><strong>Next task:</strong> ${esc(d.next_task)}</div>`;
    await loadIELTSDashboard();
  } catch(e) { document.getElementById("ieltsWritingResult").textContent = e.message; }
}

async function generateIELTSSpeaking() {
  document.getElementById("ieltsSpeakingResult").innerHTML = "";
  try {
    currentIELTSSpeaking = await api("/api/ielts/speaking/generate", {method:"POST", body:JSON.stringify({
      part: Number(document.getElementById("ieltsSpeakingPart").value),
      target_band: Number(document.getElementById("ieltsSpeakingTarget").value)
    })});
    document.getElementById("ieltsSpeakingPromptBox").classList.remove("d-none");
    document.getElementById("ieltsSpeakingTitle").textContent = currentIELTSSpeaking.title || `Speaking Part ${currentIELTSSpeaking.part}`;
    document.getElementById("ieltsSpeakingQuestions").innerHTML = `<p>${esc(currentIELTSSpeaking.instructions||"")}</p><ol>${(currentIELTSSpeaking.questions||[]).map(q=>`<li>${esc(q)}</li>`).join("")}</ol>`;
  } catch(e) { document.getElementById("ieltsSpeakingResult").textContent = e.message; }
}

async function recordIELTSSpeaking() {
  const btn = document.getElementById("ieltsMicBtn");
  if (ieltsRecorder && ieltsRecorder.state === "recording") { ieltsRecorder.stop(); return; }
  if (!navigator.mediaDevices || !window.MediaRecorder) return alert("Audio recording is not supported in this browser.");
  try {
    ieltsStream = await navigator.mediaDevices.getUserMedia({audio:true});
    const preferred = "audio/webm;codecs=opus";
    ieltsRecorder = new MediaRecorder(ieltsStream, MediaRecorder.isTypeSupported(preferred)?{mimeType:preferred}:{});
    ieltsChunks = [];
    ieltsRecorder.ondataavailable = e => { if (e.data?.size) ieltsChunks.push(e.data); };
    ieltsRecorder.onstop = async () => {
      btn.disabled = true; btn.textContent = "Transcribing…";
      ieltsStream?.getTracks().forEach(t=>t.stop());
      try {
        const mime = ieltsRecorder.mimeType || "audio/webm";
        const blob = new Blob(ieltsChunks,{type:mime});
        const form = new FormData();
        form.append("audio", blob, "ielts-speaking.webm");
        form.append("scenario", `IELTS Speaking Part ${document.getElementById("ieltsSpeakingPart").value}`);
        const d = await apiForm("/api/audio/transcribe", form);
        document.getElementById("ieltsSpeakingTranscript").value = d.transcript;
      } catch(e) { alert(e.message); }
      finally { btn.disabled=false; btn.textContent="🎙 Record answer"; ieltsRecorder=null; ieltsChunks=[]; }
    };
    ieltsRecorder.start(); btn.textContent="⏹ Stop recording";
    setTimeout(()=>{ if(ieltsRecorder?.state==="recording") ieltsRecorder.stop(); }, 120000);
  } catch(e) { alert("Microphone access failed."); }
}

async function assessIELTSSpeaking() {
  const transcript = document.getElementById("ieltsSpeakingTranscript").value.trim();
  if (!transcript) return alert("Record or type an answer first.");
  if (!currentIELTSSpeaking) return alert("Generate speaking questions first.");
  const prompt = (currentIELTSSpeaking.questions || []).join("\n");
  loading("ieltsSpeakingResult", "Assessing speaking transcript…");
  try {
    const d = await api("/api/ielts/speaking/assess", {method:"POST", body:JSON.stringify({
      part:Number(document.getElementById("ieltsSpeakingPart").value), prompt, transcript
    })});
    document.getElementById("ieltsSpeakingResult").innerHTML = `
      <div class="d-flex justify-content-between"><h4>Provisional language band ${esc(Number(d.provisional_language_band).toFixed(1))}</h4><span class="badge text-bg-warning">Pronunciation not scored</span></div>
      <div class="row g-2 my-2">${bandCard("Fluency & Coherence",d.fluency_coherence)}${bandCard("Lexical Resource",d.lexical_resource)}${bandCard("Grammar",d.grammatical_range_accuracy)}${bandCard("Pronunciation",null)}</div>
      <p class="small text-secondary">${esc(d.disclaimer)}</p>
      <h6>Strengths</h6><ul>${(d.strengths||[]).map(x=>`<li>${esc(x)}</li>`).join("")}</ul>
      <h6>Improve</h6>${(d.improvements||[]).map(x=>`<div class="mb-2"><strong>${esc(x.criterion)}:</strong> ${esc(x.issue)}<br><span class="text-secondary">${esc(x.action)}</span></div>`).join("")}
      <div class="alert alert-light"><strong>Follow-up:</strong> ${esc(d.follow_up_question)}</div>
      <button class="btn btn-sm btn-outline-primary" onclick="speakText(${JSON.stringify(String(d.follow_up_question || ""))}, this)">🔊 Listen to follow-up</button>`;
    await loadIELTSDashboard();
  } catch(e) { document.getElementById("ieltsSpeakingResult").textContent = e.message; }
}

function renderIELTSQuestions(containerId, data, section) {
  const passage = section === "reading" ? `<div class="card-soft mb-3"><h5>${esc(data.title)}</h5><div class="reading-passage">${esc(data.passage).replace(/\n/g,"<br>")}</div></div>` : `<div class="card-soft mb-3"><h5>${esc(data.title)}</h5><p class="text-secondary mb-0">Listen to the audio, then answer all questions. You may replay it during practice.</p></div>`;
  const questions = (data.questions || []).map(q => {
    const opts = (q.options || []).filter(Boolean);
    const input = opts.length ? opts.map((o,i)=>`<div class="form-check"><input class="form-check-input" type="radio" name="${section}q${q.id}" value="${esc(o)}" id="${section}q${q.id}_${i}"><label class="form-check-label" for="${section}q${q.id}_${i}">${esc(o)}</label></div>`).join("") : `<input class="form-control" name="${section}q${q.id}" placeholder="Your answer">`;
    return `<div class="card-soft my-3"><div class="fw-semibold mb-2">${esc(q.id)}. ${esc(q.question)}</div>${input}</div>`;
  }).join("");
  document.getElementById(containerId).innerHTML = passage + questions + `<button class="btn btn-primary" onclick="submitIELTSObjective('${section}')">Submit ${section}</button><div id="${section}SubmitResult" class="mt-3"></div>`;
}

async function generateIELTSReading() {
  document.getElementById("ieltsReadingContent").innerHTML = `<div class="loader">Generating reading practice…</div>`;
  try {
    currentIELTSReading = await api("/api/ielts/reading/generate", {method:"POST", body:JSON.stringify({
      test_type:document.getElementById("ieltsReadingType").value,
      target_band:Number(document.getElementById("ieltsReadingTarget").value)
    })});
    renderIELTSQuestions("ieltsReadingContent", currentIELTSReading, "reading");
  } catch(e) { document.getElementById("ieltsReadingContent").textContent=e.message; }
}

async function generateIELTSListening() {
  document.getElementById("ieltsListeningContent").innerHTML = `<div class="loader">Generating listening practice…</div>`;
  try {
    currentIELTSListening = await api("/api/ielts/listening/generate", {method:"POST", body:JSON.stringify({
      test_type:"Academic", target_band:Number(document.getElementById("ieltsListeningTarget").value)
    })});
    document.getElementById("ieltsListenAudioBtn").disabled=false;
    renderIELTSQuestions("ieltsListeningContent", currentIELTSListening, "listening");
  } catch(e) { document.getElementById("ieltsListeningContent").textContent=e.message; }
}

function playIELTSListening() {
  if (!currentIELTSListening?.audio_script) return alert("Generate a listening set first.");
  speakText(currentIELTSListening.audio_script, document.getElementById("ieltsListenAudioBtn"));
}

async function submitIELTSObjective(section) {
  const data = section === "reading" ? currentIELTSReading : currentIELTSListening;
  if (!data) return;
  const answers = {};
  (data.questions || []).forEach(q => {
    const radio = document.querySelector(`input[name="${section}q${q.id}"]:checked`);
    const text = document.querySelector(`input.form-control[name="${section}q${q.id}"]`);
    answers[String(q.id)] = radio ? radio.value : (text ? text.value : "");
  });
  const testType = section === "reading" ? document.getElementById("ieltsReadingType").value : "Academic";
  const resultId = `${section}SubmitResult`;
  document.getElementById(resultId).innerHTML = `<div class="loader">Scoring…</div>`;
  try {
    const d = await api("/api/ielts/objective/submit", {method:"POST", body:JSON.stringify({
      section, test_type:testType, title:data.title || `IELTS ${section}`,
      questions:data.questions || [], answer_key:data.answer_key || {}, explanations:data.explanations || {}, answers
    })});
    document.getElementById(resultId).innerHTML = `<div class="alert alert-primary"><strong>${esc(d.correct)}/${esc(d.total)} correct · ${esc(d.score_percent)}%</strong><br>Approximate practice band: <strong>${esc(Number(d.estimated_band).toFixed(1))}</strong><br><small>${esc(d.disclaimer)}</small></div>${(d.details||[]).map(x=>`<div class="small py-1">${x.correct?"✅":"❌"} Q${esc(x.id)} — ${esc(x.explanation||"")}</div>`).join("")}`;
    await loadIELTSDashboard();
  } catch(e) { document.getElementById(resultId).textContent=e.message; }
}

const ieltsWritingResponse = document.getElementById("ieltsWritingResponse");
if (ieltsWritingResponse) ieltsWritingResponse.addEventListener("input", () => {
  const words = ieltsWritingResponse.value.trim() ? ieltsWritingResponse.value.trim().split(/\s+/).length : 0;
  document.getElementById("ieltsWordCount").textContent = `${words} words`;
});

// Extend page navigation with IELTS dashboard refresh.
const originalOpenPage = openPage;
openPage = function(name) {
  originalOpenPage(name);
  if (name === "ielts-dashboard") loadIELTSDashboard();
};



// ========================= FULL IELTS MOCK EXAM =========================
let fullMock = null;
let mockTimerHandle = null;
let mockSecondsLeft = 0;
let mockAudioPlayed = {};
let mockSpeakingRecorder = null;
let mockSpeakingChunks = [];
let mockSpeakingStream = null;

function setMockProgress(active) {
  const order = ["listening","reading","writing","speaking","results"];
  const ai = order.indexOf(active);
  document.querySelectorAll(".mock-step").forEach(el => {
    const i = order.indexOf(el.dataset.mockStep);
    el.classList.toggle("active", i === ai);
    el.classList.toggle("done", i >= 0 && i < ai);
  });
}

function startMockTimer(seconds) {
  clearInterval(mockTimerHandle);
  mockSecondsLeft = seconds;
  const el = document.getElementById("mockTimer");
  const draw = () => {
    const m = Math.floor(mockSecondsLeft / 60);
    const s = mockSecondsLeft % 60;
    el.textContent = `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
    el.classList.toggle("time-low", mockSecondsLeft <= 300);
    if (mockSecondsLeft <= 0) {
      clearInterval(mockTimerHandle);
      el.textContent = "TIME";
      el.classList.add("time-low");
      return;
    }
    mockSecondsLeft -= 1;
  };
  draw();
  mockTimerHandle = setInterval(draw, 1000);
}

async function startFullIELTSMock() {
  const test_type = document.getElementById("mockTestType").value;
  const target_band = Number(document.getElementById("mockTargetBand").value);
  document.getElementById("mockSetup").innerHTML += `<div id="mockStartLoader" class="loader mt-3">Creating full mock exam…</div>`;
  try {
    const d = await api("/api/ielts/mock/start", {
      method:"POST",
      body:JSON.stringify({test_type, target_band})
    });
    fullMock = {...d, currentSection:"listening"};
    localStorage.setItem("activeIELTSMockId", d.mock_id);
    document.getElementById("mockSetup").classList.add("d-none");
    document.getElementById("mockExam").classList.remove("d-none");
    document.getElementById("mockExamLabel").textContent = `${d.test_type} · target ${Number(d.target_band).toFixed(1)}`;
    setMockProgress("listening");
    startMockTimer(30 * 60);
    await loadMockObjectivePart("listening", 1);
  } catch(e) {
    document.getElementById("mockStartLoader")?.remove();
    alert(e.message);
  }
}

async function resumeFullIELTSMock() {
  const id = localStorage.getItem("activeIELTSMockId");
  if (!id || fullMock) return;
  try {
    const d = await api(`/api/ielts/mock/${id}`);
    if (d.status !== "in_progress") {
      localStorage.removeItem("activeIELTSMockId");
      return;
    }
    fullMock = {
      mock_id:id,
      test_type:d.test_type,
      target_band:Number(d.metadata?.target_band || 6.5),
      status:d.status
    };
    document.getElementById("mockSetup").classList.add("d-none");
    document.getElementById("mockExam").classList.remove("d-none");
    document.getElementById("mockExamLabel").textContent = `${d.test_type} · target ${Number(fullMock.target_band).toFixed(1)}`;
    const completed = d.sections || [];
    const listeningDone = completed.filter(x=>x.section==="listening" && x.status==="completed").length;
    const readingDone = completed.filter(x=>x.section==="reading" && x.status==="completed").length;
    const writingDone = completed.filter(x=>x.section==="writing" && x.status==="completed").length;
    const speakingDone = completed.filter(x=>x.section==="speaking" && x.status==="completed").length;
    if (listeningDone < 4) {
      setMockProgress("listening"); startMockTimer(30*60); await loadMockObjectivePart("listening", listeningDone+1);
    } else if (readingDone < 3) {
      setMockProgress("reading"); startMockTimer(60*60); await loadMockObjectivePart("reading", readingDone+1);
    } else if (writingDone < 2) {
      setMockProgress("writing"); startMockTimer(60*60); await loadMockWriting();
    } else if (speakingDone < 3) {
      setMockProgress("speaking"); startMockTimer(14*60); await loadMockSpeaking();
    } else {
      await finishFullIELTSMock();
    }
  } catch(e) {
    console.warn("Could not resume IELTS mock:", e);
  }
}

function mockQuestionHTML(section, part, q) {
  const name = `mock_${section}_${part}_${q.id}`;
  const opts = (q.options || []).filter(Boolean);
  let control = "";
  if (opts.length) {
    control = opts.map((o,i)=>`
      <div class="form-check">
        <input class="form-check-input" type="radio" name="${name}" value="${esc(o)}" id="${name}_${i}">
        <label class="form-check-label" for="${name}_${i}">${esc(o)}</label>
      </div>`).join("");
  } else {
    control = `<input class="form-control" name="${name}" placeholder="Type your answer">`;
  }
  return `<div class="mock-question">
    <div class="small text-secondary mb-1">${esc(q.type || "")}${q.instruction ? ` · ${esc(q.instruction)}` : ""}</div>
    <div class="fw-semibold mb-2">${esc(q.id)}. ${esc(q.question)}</div>
    ${control}
  </div>`;
}

function collectMockAnswers(section, part, questions) {
  const answers = {};
  (questions || []).forEach(q => {
    const name = `mock_${section}_${part}_${q.id}`;
    const radio = document.querySelector(`input[name="${name}"]:checked`);
    const textInput = document.querySelector(`input[name="${name}"]:not([type="radio"])`);
    answers[String(q.id)] = radio ? radio.value : (textInput ? textInput.value.trim() : "");
  });
  return answers;
}

async function loadMockObjectivePart(section, part) {
  if (!fullMock) return;
  const ws = document.getElementById("mockWorkspace");
  ws.innerHTML = `<div class="loader">Generating ${esc(section)} ${section==="listening"?"Part":"Section"} ${part}…</div>`;
  try {
    const d = await api(`/api/ielts/mock/${fullMock.mock_id}/generate`, {
      method:"POST",
      body:JSON.stringify({section, part})
    });
    const c = d.content || {};
    fullMock.currentSection = section;
    fullMock.currentPart = part;
    fullMock.currentContent = c;
    setMockProgress(section);

    const label = section === "listening" ? `Listening Part ${part} of 4` : `Reading Section ${part} of 3`;
    const intro = section === "listening"
      ? `<div class="mock-audio-box mb-3">
           <strong>${esc(c.title || label)}</strong>
           <p class="small text-secondary mb-2">Exam mode: the recording can be started once. Answer while listening.</p>
           <button id="mockAudioBtn" class="btn btn-primary" onclick="playMockListeningOnce(${part})">▶ Play recording once</button>
           <span id="mockAudioState" class="small text-secondary ms-2"></span>
         </div>`
      : `<div class="card-soft mb-3">
           <h5>${esc(c.title || label)}</h5>
           <div class="mock-passage">${esc(c.passage || "").replace(/\n/g,"<br>")}</div>
         </div>`;

    ws.innerHTML = `
      <div class="d-flex justify-content-between align-items-center mb-2">
        <h4 class="mb-0">${label}</h4>
        <span class="badge text-bg-light">${(c.questions||[]).length} questions</span>
      </div>
      ${intro}
      ${(c.questions || []).map(q => mockQuestionHTML(section, part, q)).join("")}
      <div class="d-flex justify-content-end mt-3">
        <button class="btn btn-primary" onclick="submitMockObjectivePart('${section}', ${part})">
          Submit ${section==="listening"?"part":"section"}
        </button>
      </div>
      <div id="mockPartResult" class="mt-3"></div>`;
  } catch(e) {
    ws.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`;
  }
}

async function playMockListeningOnce(part) {
  const key = `${fullMock?.mock_id}:${part}`;
  if (mockAudioPlayed[key]) return;
  const btn = document.getElementById("mockAudioBtn");
  const state = document.getElementById("mockAudioState");
  btn.disabled = true;
  btn.textContent = "Loading audio…";
  try {
    const res = await fetch(`/api/ielts/mock/${fullMock.mock_id}/listening/${part}/audio`, {
      headers: {"Authorization":`Bearer ${currentSession.access_token}`}
    });
    if (!res.ok) {
      const d = await res.json().catch(()=>({}));
      throw new Error(d.detail || "Could not create listening audio.");
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    mockAudioPlayed[key] = true;
    btn.textContent = "Recording started";
    state.textContent = "Playing once…";
    audio.onended = () => {
      state.textContent = "Recording finished.";
      btn.textContent = "Recording played";
      URL.revokeObjectURL(url);
    };
    await audio.play();
  } catch(e) {
    mockAudioPlayed[key] = false;
    btn.disabled = false;
    btn.textContent = "▶ Play recording once";
    state.textContent = e.message;
  }
}

async function submitMockObjectivePart(section, part) {
  const c = fullMock?.currentContent || {};
  const answers = collectMockAnswers(section, part, c.questions || []);
  const unanswered = Object.values(answers).filter(x=>!String(x).trim()).length;
  if (unanswered && !confirm(`${unanswered} question(s) are unanswered. Submit anyway?`)) return;
  const resultEl = document.getElementById("mockPartResult");
  resultEl.innerHTML = `<div class="loader">Checking answers…</div>`;
  try {
    const d = await api(`/api/ielts/mock/${fullMock.mock_id}/objective/submit`, {
      method:"POST",
      body:JSON.stringify({section, part, answers})
    });
    resultEl.innerHTML = `
      <div class="alert alert-light">
        <strong>${d.part_raw_score}/${d.part_total}</strong> correct in this ${section==="listening"?"part":"section"}.
        ${d.section_band != null ? `<br><strong>${section} practice band: ${Number(d.section_band).toFixed(1)}</strong>` : ""}
      </div>`;

    const last = section === "listening" ? 4 : 3;
    if (part < last) {
      resultEl.innerHTML += `<button class="btn btn-primary" onclick="loadMockObjectivePart('${section}', ${part+1})">Continue to ${section==="listening"?"Part":"Section"} ${part+1}</button>`;
    } else if (section === "listening") {
      resultEl.innerHTML += `<button class="btn btn-primary" onclick="beginMockReading()">Continue to Reading</button>`;
    } else {
      resultEl.innerHTML += `<button class="btn btn-primary" onclick="beginMockWriting()">Continue to Writing</button>`;
    }
  } catch(e) {
    resultEl.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`;
  }
}

async function beginMockReading() {
  setMockProgress("reading");
  startMockTimer(60*60);
  await loadMockObjectivePart("reading", 1);
}

async function beginMockWriting() {
  setMockProgress("writing");
  startMockTimer(60*60);
  await loadMockWriting();
}

async function loadMockWriting() {
  const ws = document.getElementById("mockWorkspace");
  ws.innerHTML = `<div class="loader">Generating the complete Writing paper…</div>`;
  try {
    const d = await api(`/api/ielts/mock/${fullMock.mock_id}/generate`, {
      method:"POST", body:JSON.stringify({section:"writing",part:1})
    });
    const c = d.content || {};
    fullMock.writingPaper = c;
    ws.innerHTML = `
      <h4>IELTS Writing · 60 minutes</h4>
      <p class="text-secondary">Complete both tasks. Task 2 carries more weight in the Writing band.</p>
      <div class="card-soft mb-3">
        <div class="d-flex justify-content-between"><h5>${esc(c.task1?.title || "Task 1")}</h5><span>${esc(c.task1?.suggested_minutes || 20)} min</span></div>
        <p>${esc(c.task1?.prompt || "")}</p>
        <div class="small text-secondary">Write at least ${esc(c.task1?.minimum_words || 150)} words.</div>
      </div>
      <textarea id="mockWriting1" class="form-control" rows="10" placeholder="Write Task 1 here…"></textarea>
      <div id="mockWords1" class="small text-secondary text-end mt-1">0 words</div>

      <div class="card-soft my-3">
        <div class="d-flex justify-content-between"><h5>${esc(c.task2?.title || "Task 2")}</h5><span>${esc(c.task2?.suggested_minutes || 40)} min</span></div>
        <p>${esc(c.task2?.prompt || "")}</p>
        <div class="small text-secondary">Write at least ${esc(c.task2?.minimum_words || 250)} words.</div>
      </div>
      <textarea id="mockWriting2" class="form-control" rows="13" placeholder="Write Task 2 here…"></textarea>
      <div id="mockWords2" class="small text-secondary text-end mt-1">0 words</div>
      <div class="d-flex justify-content-end mt-3"><button class="btn btn-primary" onclick="submitMockWriting()">Submit Writing</button></div>
      <div id="mockWritingResult" class="mt-3"></div>`;
    const wc = (id,out) => document.getElementById(id).addEventListener("input", e => {
      const n = e.target.value.trim() ? e.target.value.trim().split(/\s+/).length : 0;
      document.getElementById(out).textContent = `${n} words`;
    });
    wc("mockWriting1","mockWords1"); wc("mockWriting2","mockWords2");
  } catch(e) { ws.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; }
}

async function submitMockWriting() {
  const t1 = document.getElementById("mockWriting1").value.trim();
  const t2 = document.getElementById("mockWriting2").value.trim();
  if (!t1 || !t2) return alert("Complete both Writing tasks before submitting.");
  const el = document.getElementById("mockWritingResult");
  el.innerHTML = `<div class="loader">Assessing both writing tasks…</div>`;
  try {
    const d = await api(`/api/ielts/mock/${fullMock.mock_id}/writing/submit`, {
      method:"POST", body:JSON.stringify({task1_response:t1, task2_response:t2})
    });
    el.innerHTML = `
      <div class="alert alert-primary"><strong>Writing practice band: ${Number(d.writing_band).toFixed(1)}</strong></div>
      <div class="row g-2">
        ${bandCard("Task 1", d.task1?.overall_band)}
        ${bandCard("Task 2", d.task2?.overall_band)}
      </div>
      <p class="small text-secondary mt-2">${esc(d.disclaimer || "")}</p>
      <button class="btn btn-primary" onclick="beginMockSpeaking()">Continue to Speaking</button>`;
  } catch(e) { el.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; }
}

async function beginMockSpeaking() {
  setMockProgress("speaking");
  startMockTimer(14*60);
  await loadMockSpeaking();
}

function speakingPromptHTML(part, obj) {
  if (part === 2) {
    return `<p>${esc(obj.instructions || "")}</p>
      <div class="alert alert-light"><strong>${esc(obj.cue_card || "")}</strong>
      <ul class="mb-0">${(obj.bullet_points||[]).map(x=>`<li>${esc(x)}</li>`).join("")}</ul></div>
      <div class="small text-secondary">Preparation: ${esc(obj.preparation_seconds || 60)} sec · Speak: up to ${esc(obj.speaking_seconds || 120)} sec</div>`;
  }
  return `<p>${esc(obj.instructions || "")}</p><ol>${(obj.questions||[]).map(q=>`<li>${esc(q)}</li>`).join("")}</ol>`;
}

async function loadMockSpeaking() {
  const ws = document.getElementById("mockWorkspace");
  ws.innerHTML = `<div class="loader">Generating complete Speaking interview…</div>`;
  try {
    const d = await api(`/api/ielts/mock/${fullMock.mock_id}/generate`, {
      method:"POST", body:JSON.stringify({section:"speaking",part:1})
    });
    const c = d.content || {};
    fullMock.speakingPaper = c;
    ws.innerHTML = `
      <h4>IELTS Speaking · Parts 1–3</h4>
      <p class="text-secondary">Answer aloud. Record each part; the audio is transcribed and the complete interview is assessed together.</p>
      ${[1,2,3].map(p=>`
        <div class="card-soft mb-3">
          <h5>Part ${p}</h5>
          ${speakingPromptHTML(p, c[`part${p}`] || {})}
          <textarea id="mockSpeaking${p}" class="form-control mt-3" rows="${p===2?6:8}" placeholder="Transcript for Part ${p}…"></textarea>
          <button id="mockSpeakBtn${p}" class="btn btn-outline-primary mt-2" onclick="recordMockSpeaking(${p})">🎙 Record Part ${p}</button>
        </div>`).join("")}
      <div class="d-flex justify-content-end"><button class="btn btn-primary" onclick="submitMockSpeaking()">Submit complete Speaking interview</button></div>
      <div id="mockSpeakingResult" class="mt-3"></div>`;
  } catch(e) { ws.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; }
}

async function recordMockSpeaking(part) {
  const btn = document.getElementById(`mockSpeakBtn${part}`);
  if (mockSpeakingRecorder && mockSpeakingRecorder.state === "recording") {
    mockSpeakingRecorder.stop();
    return;
  }
  if (!navigator.mediaDevices || !window.MediaRecorder) return alert("Audio recording is not supported in this browser.");
  try {
    mockSpeakingStream = await navigator.mediaDevices.getUserMedia({audio:true});
    const preferred = "audio/webm;codecs=opus";
    mockSpeakingRecorder = new MediaRecorder(
      mockSpeakingStream,
      MediaRecorder.isTypeSupported(preferred) ? {mimeType:preferred} : {}
    );
    mockSpeakingChunks = [];
    mockSpeakingRecorder.ondataavailable = e => { if(e.data?.size) mockSpeakingChunks.push(e.data); };
    mockSpeakingRecorder.onstop = async () => {
      btn.disabled = true; btn.textContent = "Transcribing…";
      mockSpeakingStream?.getTracks().forEach(t=>t.stop());
      try {
        const blob = new Blob(mockSpeakingChunks,{type:mockSpeakingRecorder.mimeType || "audio/webm"});
        const form = new FormData();
        form.append("audio",blob,`ielts-mock-speaking-${part}.webm`);
        form.append("scenario",`Full IELTS Mock Speaking Part ${part}`);
        const d = await apiForm("/api/audio/transcribe",form);
        document.getElementById(`mockSpeaking${part}`).value = d.transcript;
      } catch(e) { alert(e.message); }
      finally {
        btn.disabled=false; btn.textContent=`🎙 Record Part ${part}`;
        mockSpeakingRecorder=null; mockSpeakingChunks=[];
      }
    };
    mockSpeakingRecorder.start();
    btn.textContent = "⏹ Stop recording";
    const limit = part === 2 ? 130000 : 300000;
    setTimeout(()=>{ if(mockSpeakingRecorder?.state==="recording") mockSpeakingRecorder.stop(); }, limit);
  } catch(e) { alert("Microphone access failed."); }
}

async function submitMockSpeaking() {
  const transcripts = {
    "1":document.getElementById("mockSpeaking1").value.trim(),
    "2":document.getElementById("mockSpeaking2").value.trim(),
    "3":document.getElementById("mockSpeaking3").value.trim()
  };
  if (Object.values(transcripts).some(x=>!x)) return alert("Complete all three Speaking parts.");
  const el = document.getElementById("mockSpeakingResult");
  el.innerHTML = `<div class="loader">Assessing complete speaking interview…</div>`;
  try {
    const d = await api(`/api/ielts/mock/${fullMock.mock_id}/speaking/submit`, {
      method:"POST", body:JSON.stringify({transcripts})
    });
    el.innerHTML = `
      <div class="alert alert-primary"><strong>Provisional Speaking language band: ${Number(d.provisional_language_band).toFixed(1)}</strong></div>
      <div class="row g-2">
        ${bandCard("Fluency & Coherence",d.fluency_coherence)}
        ${bandCard("Lexical Resource",d.lexical_resource)}
        ${bandCard("Grammar",d.grammatical_range_accuracy)}
        ${bandCard("Pronunciation",null)}
      </div>
      <p class="small text-secondary mt-2">${esc(d.disclaimer || "Pronunciation is not assessed from transcript-only evidence.")}</p>
      <button class="btn btn-primary" onclick="finishFullIELTSMock()">Finish mock & calculate result</button>`;
  } catch(e) { el.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; }
}

async function finishFullIELTSMock() {
  if (!fullMock) return;
  clearInterval(mockTimerHandle);
  const ws = document.getElementById("mockWorkspace");
  ws.innerHTML = `<div class="loader">Calculating complete mock result…</div>`;
  try {
    const d = await api(`/api/ielts/mock/${fullMock.mock_id}/finish`, {method:"POST",body:"{}"});
    setMockProgress("results");
    const b = d.bands || {};
    ws.innerHTML = `
      <div class="text-center mb-4">
        <div class="small text-secondary">PRACTICE OVERALL BAND</div>
        <div class="mock-band">${Number(d.overall_band).toFixed(1)}</div>
        <div class="text-secondary">${esc(fullMock.test_type || "IELTS")} Full Mock</div>
      </div>
      <div class="row g-3 mb-4">
        ${bandCard("Listening",b.listening)}
        ${bandCard("Reading",b.reading)}
        ${bandCard("Writing",b.writing)}
        ${bandCard("Speaking*",b.speaking)}
      </div>
      <div class="alert alert-warning">${esc(d.disclaimer)}</div>
      <div class="card-soft">
        <h5>What this result means</h5>
        <p class="mb-2">Listening and Reading use 40-question raw scores with practice band conversion. Writing uses AI feedback with Task 2 weighted more heavily. Speaking is provisional because pronunciation is not acoustically scored.</p>
        <button class="btn btn-primary" onclick="resetFullIELTSMock()">Start another full mock</button>
        <button class="btn btn-outline-primary ms-2" onclick="openPage('ielts-dashboard')">IELTS Dashboard</button>
      </div>`;
    localStorage.removeItem("activeIELTSMockId");
    await loadIELTSDashboard();
  } catch(e) { ws.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; }
}

function resetFullIELTSMock() {
  clearInterval(mockTimerHandle);
  fullMock = null;
  mockAudioPlayed = {};
  localStorage.removeItem("activeIELTSMockId");
  document.getElementById("mockExam").classList.add("d-none");
  document.getElementById("mockSetup").classList.remove("d-none");
  location.reload();
}

// Extend navigation again so an in-progress full mock can be resumed.
const openPageBeforeFullMock = openPage;
openPage = function(name) {
  openPageBeforeFullMock(name);
  if (name === "ielts-mock") resumeFullIELTSMock();
};
