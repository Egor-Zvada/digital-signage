function getCookie(name) {
  return document.cookie.split(';').map((value) => value.trim()).find((value) => value.startsWith(`${name}=`))?.slice(name.length + 1) || '';
}

document.querySelectorAll('[data-dialog-open]').forEach((button) => {
  button.addEventListener('click', () => document.getElementById(button.dataset.dialogOpen)?.showModal());
});
document.querySelectorAll('[data-dialog-close]').forEach((button) => {
  button.addEventListener('click', () => button.closest('dialog')?.close());
});
document.querySelectorAll('form[data-confirm]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});
document.querySelectorAll('[data-copy-target]').forEach((button) => {
  button.addEventListener('click', async () => {
    const input = document.getElementById(button.dataset.copyTarget);
    if (!input) return;
    await navigator.clipboard.writeText(input.value);
    button.textContent = 'Скопировано';
  });
});

const sortable = document.getElementById('playlist-items');
if (sortable) {
  let dragged = null;
  sortable.querySelectorAll('.sortable-item').forEach((item) => {
    item.addEventListener('dragstart', () => {
      dragged = item;
      item.classList.add('dragging');
    });
    item.addEventListener('dragend', async () => {
      item.classList.remove('dragging');
      dragged = null;
      const ids = [...sortable.querySelectorAll('.sortable-item')].map((row) => row.dataset.id);
      const response = await fetch(sortable.dataset.reorderUrl, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': decodeURIComponent(getCookie('csrftoken'))},
        body: JSON.stringify({ids}),
      });
      if (!response.ok) window.location.reload();
      sortable.querySelectorAll('.item-index').forEach((node, index) => node.textContent = index + 1);
    });
  });
  sortable.addEventListener('dragover', (event) => {
    event.preventDefault();
    if (!dragged) return;
    const siblings = [...sortable.querySelectorAll('.sortable-item:not(.dragging)')];
    const next = siblings.find((row) => event.clientY < row.getBoundingClientRect().top + row.offsetHeight / 2);
    sortable.insertBefore(dragged, next || null);
  });
}

window.setTimeout(() => document.querySelectorAll('.messages .message').forEach((message) => message.remove()), 6000);

