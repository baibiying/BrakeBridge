document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const input = document.querySelector(button.dataset.copy);
    const status = button.form.querySelector(".copy-status");
    if (!input.value.trim()) {
      status.textContent = "请先生成或填写草稿。";
      return;
    }
    try {
      await navigator.clipboard.writeText(input.value);
      status.textContent = "已复制草稿。";
    } catch {
      input.focus();
      input.select();
      status.textContent = "已选中草稿，可使用 Ctrl+C 复制。";
    }
  });
});
document.querySelectorAll("[data-open-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    const tab = document.getElementById(button.dataset.openTab);
    if (tab) {
      tab.click();
      tab.focus();
    }
  });
});

const replyPanel = document.querySelector("[data-reply-status]");
if (replyPanel) {
  const followup = document.getElementById("record-follow");
  const show = (target, field, text) => {
    const element = target.querySelector(`[data-reply-${field}]`);
    if (element) {
      element.textContent = text;
      element.hidden = !text;
    }
  };
  const readReply = async () => {
    try {
      const response = await fetch(replyPanel.dataset.replyStatus, { cache: "no-store" });
      if (!response.ok) return;
      const reply = await response.json();
      const complete = ["sent", "attention", "uncertain"].includes(reply.stage);
      const sent = reply.stage === "sent";
      show(replyPanel, "label", reply.label);
      show(replyPanel, "note", reply.note || (sent ? "下一步：人工核实采购需求" : "下一步：查看回复和待核实信息"));
      show(replyPanel, "action", sent ? "继续跟进" : "查看回复");
      show(followup, "label", reply.label);
      show(followup, "body", reply.body);
      show(followup, "note", reply.note);
      show(followup, "id", reply.reply_id ? `平台回复 ID ${reply.reply_id} · ${reply.updated_at}` : "");
      replyPanel.classList.toggle("needs-review", ["attention", "uncertain"].includes(reply.stage));
      if (!complete) window.setTimeout(readReply, 2500);
    } catch {
      window.setTimeout(readReply, 5000);
    }
  };
  window.setTimeout(readReply, 2500);
}
