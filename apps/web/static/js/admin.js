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
    const status = document.getElementById(button.dataset.copyStatus);
    let copied = false;
    try {
      await navigator.clipboard.writeText(input.value);
      copied = true;
    } catch {
      input.focus();
      input.select();
      input.setSelectionRange(0, input.value.length);
      try { copied = document.execCommand('copy'); } catch { /* Keep the URL selected. */ }
    }
    button.textContent = copied ? 'Скопировано' : 'Выделено';
    if (status) status.textContent = copied
      ? 'Ссылка скопирована. Вставьте её в настройку браузера дисплея.'
      : 'Браузер запретил копирование. Ссылка выделена — нажмите Ctrl+C или скопируйте её через меню.';
  });
});

document.querySelectorAll('[data-asset-upload-form]').forEach((form) => {
  const fileInput = form.querySelector('[data-asset-file]');
  const urlInput = form.querySelector('[data-asset-url]');
  const websiteMode = form.querySelector('[data-website-mode]');
  const hint = form.querySelector('[data-asset-kind-hint]');

  const fileKind = (file) => {
    if (!file) return '';
    if (file.type.startsWith('image/')) return 'Фото';
    if (file.type.startsWith('video/')) return 'Видео';
    const extension = file.name.split('.').pop()?.toLowerCase();
    if (['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'tif', 'tiff'].includes(extension)) return 'Фото';
    if (['mp4', 'm4v', 'mov', 'webm', 'mkv', 'avi', 'mpeg', 'mpg'].includes(extension)) return 'Видео';
    return 'Файл будет проверен сервером';
  };

  const updateHint = () => {
    const file = fileInput?.files?.[0];
    const hasUrl = Boolean(urlInput?.value.trim());
    if (file) hint.textContent = `Определён тип: ${fileKind(file)}. Сервер проверит содержимое после загрузки.`;
    else if (hasUrl) hint.textContent = 'Определён тип: Сайт.';
    else hint.textContent = 'Загрузите фото или видео либо вставьте ссылку — тип определится автоматически.';
    if (websiteMode) websiteMode.closest('p').hidden = !hasUrl || Boolean(file);
  };

  fileInput?.addEventListener('change', updateHint);
  urlInput?.addEventListener('input', updateHint);
  updateHint();
});

document.querySelectorAll('[data-playlist-item-form]').forEach((form) => {
  const itemType = form.querySelector('#id_item_type');
  const assetSelect = form.querySelector('#id_asset');
  const sceneSelect = form.querySelector('#id_scene');
  const durationInput = form.querySelector('#id_duration_seconds');

  const applyAssetDefaults = () => {
    const option = assetSelect?.selectedOptions?.[0];
    if (!option?.value || !durationInput) return;
    const durationMs = Number(option.dataset.durationMs || 0);
    durationInput.value = option.dataset.kind === 'video' && durationMs > 0
      ? Math.max(1, Math.ceil(durationMs / 1000))
      : 10;
  };

  assetSelect?.addEventListener('change', () => {
    if (!assetSelect.value) return;
    if (itemType) itemType.value = 'asset';
    if (sceneSelect) sceneSelect.value = '';
    applyAssetDefaults();
  });
  sceneSelect?.addEventListener('change', () => {
    if (!sceneSelect.value) return;
    if (itemType) itemType.value = 'scene';
    if (assetSelect) assetSelect.value = '';
    if (durationInput) durationInput.value = 10;
  });
  applyAssetDefaults();
});

document.querySelectorAll('[data-screen-editor]').forEach((form) => {
  const mode = form.querySelector('[name="display_mode"]');
  const updateMode = () => {
    form.querySelectorAll('[data-screen-modes]').forEach((group) => {
      group.hidden = !group.dataset.screenModes.split(/\s+/).includes(mode?.value || 'always');
    });
  };
  mode?.addEventListener('change', updateMode);
  updateMode();
});

document.querySelectorAll('[data-widget-editor]').forEach((form) => {
  const typeSelect = form.querySelector('#id_scene_type');
  const updateVisibleFields = () => {
    const selectedType = typeSelect?.value || '';
    form.querySelectorAll('[data-widget-types]').forEach((group) => {
      const supported = group.dataset.widgetTypes.split(/\s+/);
      group.hidden = selectedType && !supported.includes(selectedType);
    });
  };
  typeSelect?.addEventListener('change', updateVisibleFields);
  updateVisibleFields();
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
      try {
        const response = await fetch(sortable.dataset.reorderUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': document.querySelector('[name="csrfmiddlewaretoken"]')?.value || '',
          },
          body: JSON.stringify({ids}),
        });
        if (!response.ok) throw new Error('Не удалось сохранить порядок');
        sortable.querySelectorAll('.item-index').forEach((node, index) => node.textContent = index + 1);
      } catch {
        window.alert('Не удалось сохранить порядок. Страница будет обновлена, чтобы показать сохранённую очередь.');
        window.location.reload();
      }
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
