(function () {
    "use strict";

    var socket = null;
    var sessions = {};
    var selectedSessionId = null;
    var filterStatus = "all";

    var $ = function (sel) { return document.querySelector(sel); };

    var dom = {
        sessionList: $("#session-list"),
        chatEmpty: $("#chat-empty"),
        chatActive: $("#chat-active"),
        chatMessages: $("#chat-messages"),
        chatSessionId: $("#chat-session-id"),
        chatStatus: $("#chat-status"),
        chatModel: $("#chat-model"),
        chatTime: $("#chat-time"),
        replyInput: $("#reply-input"),
        replyArea: $("#reply-area"),
        replyHint: $("#reply-hint"),
        btnSendReply: $("#btn-send-reply"),
        btnRefresh: $("#btn-refresh"),
        filterStatus: $("#filter-status"),
        pendingCount: $("#pending-count"),
        repliedCount: $("#replied-count"),
        totalCount: $("#total-count"),
        wsStatus: $("#ws-status"),
        settingApiKey: $("#setting-api-key"),
        settingTimeout: $("#setting-timeout"),
        settingTimeoutReply: $("#setting-timeout-reply"),
        btnSaveConfig: $("#btn-save-config"),
        btnClearAll: $("#btn-clear-all"),
        toastContainer: $("#toast-container"),
    };

    function toast(msg, type) {
        if (type === undefined) type = "info";
        var el = document.createElement("div");
        el.className = "toast " + type;
        el.textContent = msg;
        dom.toastContainer.appendChild(el);
        setTimeout(function () {
            el.style.opacity = "0";
            el.style.transform = "translateX(30px)";
            el.style.transition = "all 0.3s ease";
            setTimeout(function () { el.remove(); }, 300);
        }, 3000);
    }

    function formatTime(isoStr) {
        if (!isoStr) return "";
        try {
            var d = new Date(isoStr);
            var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
            return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
        } catch (e) { return isoStr; }
    }

    function formatDateTime(isoStr) {
        if (!isoStr) return "";
        try {
            var d = new Date(isoStr);
            var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
            return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " +
                pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
        } catch (e) { return isoStr; }
    }

    function escapeHtml(text) {
        var div = document.createElement("div");
        div.appendChild(document.createTextNode(text));
        return div.innerHTML;
    }

    function statusLabel(status) {
        var map = { waiting: "等待回复", replied: "已回复", timeout: "已超时" };
        return map[status] || status;
    }

    function initSocket() {
        socket = io({
            transports: ["websocket", "polling"],
            reconnection: true,
            reconnectionDelay: 2000,
        });

        socket.on("connect", function () {
            dom.wsStatus.className = "ws-status connected";
            dom.wsStatus.innerHTML = '<span class="ws-dot"></span><span>已连接</span>';
        });

        socket.on("disconnect", function () {
            dom.wsStatus.className = "ws-status";
            dom.wsStatus.innerHTML = '<span class="ws-dot"></span><span>已断开</span>';
        });

        socket.on("init_data", function (data) {
            if (data.sessions) {
                data.sessions.forEach(function (s) { sessions[s.id] = s; });
                renderSessionList();
            }
            if (data.config) { applyConfig(data.config); }
            updateStats();
        });

        socket.on("new_request", function (data) {
            var s = data.session;
            sessions[s.id] = s;
            renderSessionList();
            updateStats();
            toast("新消息: " + (data.query_preview || "").substring(0, 40) + "...", "warning");
            playBeep();
        });

        socket.on("session_updated", function (s) {
            sessions[s.id] = s;
            renderSessionList();
            updateStats();
            if (selectedSessionId === s.id) {
                renderChatPanel(s.id);
            }
        });

        socket.on("sessions_list", function (data) {
            if (data.sessions) {
                sessions = {};
                data.sessions.forEach(function (s) { sessions[s.id] = s; });
                renderSessionList();
                updateStats();
                if (selectedSessionId && !sessions[selectedSessionId]) {
                    deselectSession();
                }
            }
        });

        socket.on("messages_data", function (data) {
            if (sessions[data.session_id]) {
                sessions[data.session_id].messages = data.messages;
                if (selectedSessionId === data.session_id) {
                    renderMessages(data.messages);
                }
            }
        });

        socket.on("error", function (data) {
            toast(data.message || "未知错误", "error");
        });
    }

    function playBeep() {
        try {
            var ctx = new (window.AudioContext || window.webkitAudioContext)();
            var osc = ctx.createOscillator();
            var gain = ctx.createGain();
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.frequency.value = 800;
            gain.gain.value = 0.08;
            osc.start();
            osc.stop(ctx.currentTime + 0.15);
        } catch (e) { }
    }

    function renderSessionList() {
        var list = Object.values(sessions);
        list.sort(function (a, b) {
            if (a.status === "waiting" && b.status !== "waiting") return -1;
            if (a.status !== "waiting" && b.status === "waiting") return 1;
            return (b.created_at || "").localeCompare(a.created_at || "");
        });

        if (filterStatus !== "all") {
            list = list.filter(function (s) { return s.status === filterStatus; });
        }

        if (list.length === 0) {
            dom.sessionList.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#128172;</div>暂无会话，等待外部请求...</div>';
            return;
        }

        var html = "";
        list.forEach(function (s) {
            var activeClass = s.id === selectedSessionId ? " active" : "";
            var preview = getSessionPreview(s);
            var avatar = s.status === "waiting" ? "&#128100;" : "&#129302;";
            html += '<div class="session-item' + activeClass + '" data-id="' + s.id + '">' +
                '<div class="session-item-header">' +
                '<span class="session-id">' + avatar + " " + escapeHtml(s.id.substring(0, 12)) + '</span>' +
                '<span class="session-status ' + s.status + '">' + statusLabel(s.status) + '</span>' +
                '</div>' +
                '<div class="session-preview">' + escapeHtml(preview) + '</div>' +
                '<div class="session-meta">' +
                '<span>' + escapeHtml(s.model || "") + '</span>' +
                '<span>' + formatTime(s.created_at) + '</span>' +
                '</div>' +
                '</div>';
        });
        dom.sessionList.innerHTML = html;

        dom.sessionList.querySelectorAll(".session-item").forEach(function (el) {
            el.addEventListener("click", function () {
                selectSession(el.getAttribute("data-id"));
            });
        });
    }

    function getSessionPreview(s) {
        if (s.messages && s.messages.length > 0) {
            for (var i = s.messages.length - 1; i >= 0; i--) {
                if (s.messages[i].role === "user") {
                    return (s.messages[i].content || "").substring(0, 60);
                }
            }
        }
        return "(无消息内容)";
    }

    function selectSession(sessionId) {
        selectedSessionId = sessionId;
        renderSessionList();
        renderChatPanel(sessionId);
    }

    function deselectSession() {
        selectedSessionId = null;
        dom.chatEmpty.style.display = "flex";
        dom.chatActive.style.display = "none";
        renderSessionList();
    }

    function renderChatPanel(sessionId) {
        var session = sessions[sessionId];
        if (!session) { deselectSession(); return; }

        dom.chatEmpty.style.display = "none";
        dom.chatActive.style.display = "flex";

        dom.chatSessionId.textContent = session.id;
        dom.chatStatus.textContent = statusLabel(session.status);
        dom.chatStatus.className = "chat-status " + session.status;
        dom.chatModel.textContent = session.model || "";
        dom.chatTime.textContent = formatDateTime(session.created_at);

        if (!session.messages || session.messages.length === 0) {
            socket.emit("request_messages", { session_id: sessionId });
        } else {
            renderMessages(session.messages);
        }

        if (session.status === "waiting") {
            dom.replyArea.classList.remove("disabled");
            dom.replyInput.disabled = false;
            dom.btnSendReply.disabled = false;
            dom.replyInput.placeholder = "输入回复内容... Ctrl+Enter 发送";
            dom.replyHint.textContent = "回复将以 AI 身份返回给调用方";
            dom.replyInput.focus();
        } else {
            dom.replyArea.classList.add("disabled");
            dom.replyInput.disabled = true;
            dom.btnSendReply.disabled = true;
            dom.replyInput.placeholder = "该会话已" + statusLabel(session.status) + "，无法回复";
            dom.replyHint.textContent = "";
        }

        dom.replyInput.value = "";
    }

    function renderMessages(messages) {
        if (!messages || messages.length === 0) {
            dom.chatMessages.innerHTML = '<div class="empty-state">暂无消息</div>';
            return;
        }

        var html = "";
        messages.forEach(function (msg, idx) {
            var role = msg.role || "user";
            var content = msg.content || "";
            var delay = Math.min(idx * 0.05, 0.3);

            if (role === "system") {
                html += '<div class="message system" style="animation-delay:' + delay + 's">[系统] ' + escapeHtml(content) + '</div>';
            } else if (role === "user") {
                html += '<div class="message-row user" style="animation: msg-in 0.3s cubic-bezier(0.34,1.56,0.64,1) ' + delay + 's both">' +
                    '<div class="message-avatar">&#128100;</div>' +
                    '<div class="message user">' +
                    '<div class="message-label">用户</div>' +
                    escapeHtml(content) +
                    '</div>' +
                    '</div>';
            } else if (role === "assistant") {
                html += '<div class="message-row assistant" style="animation: msg-in 0.3s cubic-bezier(0.34,1.56,0.64,1) ' + delay + 's both">' +
                    '<div class="message-avatar">&#129302;</div>' +
                    '<div class="message assistant">' +
                    '<div class="message-label">AI（你）</div>' +
                    escapeHtml(content) +
                    '</div>' +
                    '</div>';
            }
        });

        dom.chatMessages.innerHTML = html;
        dom.chatMessages.scrollTop = dom.chatMessages.scrollHeight;
    }

    function sendReply() {
        var content = dom.replyInput.value.trim();
        if (!content) { toast("回复内容不能为空", "error"); return; }
        if (!selectedSessionId) { toast("请先选择一个会话", "error"); return; }

        dom.btnSendReply.disabled = true;
        dom.btnSendReply.textContent = "发送中...";

        fetch("/api/admin/reply", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: selectedSessionId, content: content }),
        })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.success) {
                    toast("回复已发送", "success");
                    dom.replyInput.value = "";
                    if (sessions[selectedSessionId]) {
                        sessions[selectedSessionId].status = "replied";
                        if (!sessions[selectedSessionId].messages) sessions[selectedSessionId].messages = [];
                        sessions[selectedSessionId].messages.push({ role: "assistant", content: content });
                        renderChatPanel(selectedSessionId);
                        renderSessionList();
                        updateStats();
                    }
                } else {
                    toast(data.error || "发送失败", "error");
                }
            })
            .catch(function (err) { toast("网络错误: " + err.message, "error"); })
            .finally(function () {
                dom.btnSendReply.disabled = false;
                dom.btnSendReply.textContent = "发送";
            });
    }

    function updateStats() {
        var all = Object.values(sessions);
        var total = all.length;
        var waiting = 0;
        var replied = 0;
        all.forEach(function (s) {
            if (s.status === "waiting") waiting++;
            else if (s.status === "replied") replied++;
        });
        dom.pendingCount.textContent = waiting;
        dom.repliedCount.textContent = replied;
        dom.totalCount.textContent = total;
    }

    function applyConfig(cfg) {
        if (cfg.api_key && cfg.api_key !== "***") dom.settingApiKey.value = cfg.api_key;
        if (cfg.timeout) dom.settingTimeout.value = cfg.timeout;
        if (cfg.timeout_reply) dom.settingTimeoutReply.value = cfg.timeout_reply;
    }

    function loadConfig() {
        fetch("/api/admin/config")
            .then(function (res) { return res.json(); })
            .then(function (data) { applyConfig(data); })
            .catch(function () { });
    }

    function saveConfig() {
        var updates = {};
        var apiKey = dom.settingApiKey.value.trim();
        var timeout = parseInt(dom.settingTimeout.value, 10);
        var timeoutReply = dom.settingTimeoutReply.value.trim();

        if (apiKey) updates.api_key = apiKey;
        if (!isNaN(timeout) && timeout >= 10) updates.timeout = timeout;
        if (timeoutReply) updates.timeout_reply = timeoutReply;

        if (Object.keys(updates).length === 0) { toast("没有需要保存的设置", "warning"); return; }

        fetch("/api/admin/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(updates),
        })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.success) toast("设置已保存", "success");
                else toast(data.error || "保存失败", "error");
            })
            .catch(function (err) { toast("网络错误: " + err.message, "error"); });
    }

    function clearAllSessions() {
        if (!confirm("确定要清空所有会话历史吗？此操作不可撤销。")) return;

        fetch("/api/admin/clear", { method: "POST" })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.success) {
                    sessions = {};
                    selectedSessionId = null;
                    renderSessionList();
                    deselectSession();
                    updateStats();
                    toast("所有会话已清空", "success");
                } else {
                    toast(data.error || "操作失败", "error");
                }
            })
            .catch(function (err) { toast("网络错误: " + err.message, "error"); });
    }

    function bindEvents() {
        dom.btnSendReply.addEventListener("click", sendReply);

        dom.replyInput.addEventListener("keydown", function (e) {
            if (e.ctrlKey && e.key === "Enter") {
                e.preventDefault();
                sendReply();
            }
        });

        dom.btnRefresh.addEventListener("click", function () {
            socket.emit("request_sessions");
            toast("已刷新", "info");
        });

        dom.filterStatus.addEventListener("change", function () {
            filterStatus = this.value;
            renderSessionList();
        });

        dom.btnSaveConfig.addEventListener("click", saveConfig);
        dom.btnClearAll.addEventListener("click", clearAllSessions);
    }

    function init() {
        initSocket();
        bindEvents();
        loadConfig();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
