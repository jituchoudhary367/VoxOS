/**
 * VoxOS Dashboard Logic
 */

document.addEventListener('DOMContentLoaded', () => {
    // API URL Base
    const API_URL = 'http://localhost:8000/api/v1';
    let sessionId = crypto.randomUUID();

    // --- Navigation Logic ---
    const navLinks = document.querySelectorAll('.sidebar nav a');
    const views = document.querySelectorAll('.view');

    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            // Update active nav state
            navLinks.forEach(l => l.parentElement.classList.remove('active'));
            e.target.parentElement.classList.add('active');
            
            // Switch view
            const targetId = e.target.getAttribute('href').substring(1);
            views.forEach(v => v.classList.remove('active'));
            document.getElementById(`view-${targetId}`).classList.add('active');

            // Refresh view specific data
            if (targetId === 'dashboard') fetchTasks();
            if (targetId === 'approvals') fetchApprovals();
        });
    });


    // --- Chat Logic ---
    const chatInput = document.getElementById('chat-input');
    const btnSend = document.getElementById('btn-send');
    const chatMessages = document.getElementById('chat-messages');

    function addMessage(text, type = 'user') {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${type}`;
        msgDiv.textContent = text;
        chatMessages.appendChild(msgDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    async function sendMessage() {
        const text = chatInput.value.trim();
        if (!text) return;

        addMessage(text, 'user');
        chatInput.value = '';
        chatInput.disabled = true;
        btnSend.disabled = true;

        try {
            const res = await fetch(`${API_URL}/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, session_id: sessionId })
            });
            const data = await res.json();
            
            if (data.requires_approval) {
                addMessage("This action requires your approval. Please check the Approvals tab.", 'system');
                fetchApprovals();
            } else {
                addMessage(data.response, 'agent');
            }
            
            sessionId = data.session_id; // persist session
            fetchTasks(); // Refresh tasks list
            
        } catch (error) {
            console.error(error);
            addMessage("Failed to connect to VoxOS API.", 'system');
        } finally {
            chatInput.disabled = false;
            btnSend.disabled = false;
            chatInput.focus();
        }
    }

    btnSend.addEventListener('click', sendMessage);
    chatInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });


    // --- Tasks Logic ---
    const taskList = document.getElementById('recent-task-list');
    
    async function fetchTasks() {
        try {
            const res = await fetch(`${API_URL}/tasks?session_id=${sessionId}&limit=10`);
            const data = await res.json();
            
            if (data.length === 0) {
                taskList.innerHTML = '<li class="empty-state">No recent tasks</li>';
                return;
            }

            taskList.innerHTML = '';
            let activeCount = 0;

            data.forEach(task => {
                if (['CREATED', 'UNDERSTANDING', 'PLANNING', 'EXECUTING', 'VERIFYING'].includes(task.status)) {
                    activeCount++;
                }

                const li = document.createElement('li');
                let statusClass = 'status-created';
                if (task.status === 'COMPLETED') statusClass = 'status-completed';
                else if (task.status === 'FAILED' || task.status === 'CANCELLED') statusClass = 'status-failed';
                else if (task.status === 'WAITING_FOR_APPROVAL') statusClass = 'status-waiting';
                else if (task.status === 'EXECUTING') statusClass = 'status-executing';

                li.innerHTML = `
                    <div style="display:flex; justify-content: space-between;">
                        <strong>${task.intent || 'Unknown Intent'}</strong>
                        <span class="task-status ${statusClass}">${task.status}</span>
                    </div>
                    <div style="color: var(--text-muted); font-size: 13px;">${task.user_request.substring(0, 50)}${task.user_request.length > 50 ? '...' : ''}</div>
                `;
                taskList.appendChild(li);
            });

            document.getElementById('stat-active-tasks').textContent = activeCount;
            
        } catch (error) {
            console.error('Failed to fetch tasks:', error);
        }
    }

    document.getElementById('btn-refresh-tasks').addEventListener('click', fetchTasks);


    // --- Approvals Logic ---
    const approvalsContainer = document.getElementById('approvals-list-container');
    const badge = document.getElementById('approval-badge');
    const pendingStat = document.getElementById('stat-pending-approvals');

    async function fetchApprovals() {
        try {
            const res = await fetch(`${API_URL}/approvals`);
            const data = await res.json();
            
            const count = data.length;
            badge.textContent = count;
            badge.style.display = count > 0 ? 'inline-block' : 'none';
            pendingStat.textContent = count;
            
            if (count === 0) {
                approvalsContainer.innerHTML = '<div class="empty-state">No pending approvals.</div>';
                return;
            }

            approvalsContainer.innerHTML = '';
            data.forEach(app => {
                const div = document.createElement('div');
                div.className = 'approval-item';
                div.innerHTML = `
                    <div class="approval-details">
                        <h4>${app.requested_action}</h4>
                        <p style="color: var(--text-muted); font-size: 14px; margin-top: 5px;">${app.reason}</p>
                        <div style="margin-top: 10px; font-size: 12px;">
                            <span style="background: var(--danger); padding: 2px 6px; border-radius: 4px; color: white;">${app.risk_level} RISK</span>
                            <span style="color: var(--text-muted); margin-left: 10px;">Tool: ${app.tool_name}</span>
                        </div>
                    </div>
                    <div class="approval-actions">
                        <button class="btn btn-success btn-approve" data-id="${app.id}">Approve</button>
                        <button class="btn btn-danger btn-reject" data-id="${app.id}">Reject</button>
                    </div>
                `;
                approvalsContainer.appendChild(div);
            });

            // Bind buttons
            document.querySelectorAll('.btn-approve').forEach(btn => {
                btn.addEventListener('click', () => handleApproval(btn.dataset.id, true));
            });
            document.querySelectorAll('.btn-reject').forEach(btn => {
                btn.addEventListener('click', () => handleApproval(btn.dataset.id, false));
            });
            
        } catch (error) {
            console.error('Failed to fetch approvals:', error);
        }
    }

    async function handleApproval(id, approved) {
        try {
            const res = await fetch(`${API_URL}/approvals/${id}/respond`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ approved })
            });
            if (!res.ok) throw new Error(`Approval request failed: ${res.status}`);
            fetchApprovals();
            fetchTasks();
            if (approved) {
                addMessage("Approval granted. Continuing execution.", "system");
            } else {
                addMessage("Action rejected. Task cancelled.", "system");
            }
        } catch (error) {
            console.error('Approval error:', error);
        }
    }

    // Initial load
    fetchTasks();
    fetchApprovals();
    
    // Poll for updates (simple alternative to WebSockets for UI demo)
    setInterval(() => {
        fetchTasks();
        fetchApprovals();
    }, 5000);
});
