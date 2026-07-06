// ── Handle advice quick actions ──
function handleAdviceAction(action) {
  if (action === 'dashboard_goal:backlog') { startGoalQuick('Backlog bijwerken'); return; }
  if (action === 'dashboard_goal:featurex') { startGoalQuick('Feature X uitwerpen'); return; }
  if (action === 'dashboard_goal:bugfixes') { startGoalQuick('Bugfixes afhandelen'); return; }
  if (action === 'run_scan') { runDemandScan(); return; }
  if (action === 'generate_suggestions') { generateSuggestions(); return; }
  if (action === 'new_goal') { showNewGoalForm(); return; }
  if (action === 'write_all_kansen') { writeAllNewKansen(); return; }
  if (action.startsWith('retry_goal:')) { retryFailedGoal(action.split(':')[1]); return; }
  if (action.startsWith('open_tab:')) { switchView(action.split(':')[1]); return; }
  if (action.startsWith('write_article:')) {
    var keyword = action.split(':').slice(1).join(':');
    writeArticleForKeyword(keyword);
    return;
  }
  console.warn('Onbekende action:', action);
}

// ── Quick goal creation (direct in DB) ──
function startGoalQuick(title) {
  if (!confirm('Start actiepunt: "' + title + '"?')) return;
  
  fetch('/api/goals', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      title: 'Actiepunt: ' + title,
      objective: 'Uitvoeren',
      project: 'Dashboard'
    })
  }).then(r => r.json()).then(function(data) {
    if (data.goal_id) {
      fetch('/api/goals/confirm', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({goal_id: data.goal_id})
      }).then(() => loadCurrentTab());
      alert('Actiepunt gestart!');
    } else {
      alert('Fout: ' + (data.detail || 'onbekend'));
    }
  });
}