<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue';

import {
  listVersionPoliciesApi,
  saveVersionPolicyApi,
  type VersionPolicyItem,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Select as ASelect,
  Switch as ASwitch,
  Table as ATable,
  Tag as ATag,
  message,
} from 'ant-design-vue';

interface VersionPolicyForm {
  appCode: string;
  target: string;
  platform: string;
  channel: string;
  latestVersion: string;
  minSupportedVersion: string;
  enforceExactMatch: boolean;
  forceUpgrade: boolean;
  autoUpgradeWithoutConfirm: boolean;
  title: string;
  details: string;
  downloadUrl: string;
  releaseNotes: string;
  publishedAt: string;
  updaterUrl: string;
  updaterSignature: string;
  updaterPubkey: string;
}

const loading = ref(false);
const saving = ref(false);
const rows = ref<VersionPolicyItem[]>([]);

const formState = reactive<VersionPolicyForm>({
  appCode: 'AiceMind',
  target: 'backtest-desktop',
  platform: 'all',
  channel: 'stable',
  latestVersion: '',
  minSupportedVersion: '',
  enforceExactMatch: true,
  forceUpgrade: true,
  autoUpgradeWithoutConfirm: false,
  title: '发现新版本，请升级后继续使用',
  details: '已发布新版本，点击下方按钮即可下载。',
  downloadUrl: '',
  releaseNotes: '',
  publishedAt: '',
  updaterUrl: '',
  updaterSignature: '',
  updaterPubkey: '',
});

const publicDownloadPageUrl = computed(() => {
  const params = new URLSearchParams({
    appCode: formState.appCode || 'AiceMind',
    target: formState.target || 'backtest-desktop',
    platform: formState.platform || 'all',
    channel: formState.channel || 'stable',
  });
  return `${window.location.origin}/admin-api/public/download-page?${params.toString()}`;
});

const columns = [
  { title: '应用', dataIndex: 'appCode', key: 'appCode', width: 120 },
  { title: '目标', dataIndex: 'target', key: 'target', width: 150 },
  { title: '平台', dataIndex: 'platform', key: 'platform', width: 100 },
  { title: '渠道', dataIndex: 'channel', key: 'channel', width: 100 },
  { title: '最新版本', dataIndex: 'latestVersion', key: 'latestVersion', width: 120 },
  { title: '最低支持版本', dataIndex: 'minSupportedVersion', key: 'minSupportedVersion', width: 140 },
  { title: '下载地址', dataIndex: 'downloadUrl', key: 'downloadUrl', ellipsis: true },
  { title: '更新时间', dataIndex: 'updatedAt', key: 'updatedAt', width: 180 },
  { title: '操作', key: 'action', width: 120 },
];

const loadRows = async () => {
  loading.value = true;
  try {
    rows.value = await listVersionPoliciesApi();
  } finally {
    loading.value = false;
  }
};

const fillForm = (row?: Partial<VersionPolicyItem>) => {
  formState.appCode = row?.appCode || 'AiceMind';
  formState.target = row?.target || 'backtest-desktop';
  formState.platform = row?.platform || 'all';
  formState.channel = row?.channel || 'stable';
  formState.latestVersion = row?.latestVersion || '';
  formState.minSupportedVersion = row?.minSupportedVersion || '';
  formState.enforceExactMatch = row?.enforceExactMatch ?? true;
  formState.forceUpgrade = row?.forceUpgrade ?? true;
  formState.autoUpgradeWithoutConfirm = row?.autoUpgradeWithoutConfirm ?? false;
  formState.title = row?.title || '发现新版本，请升级后继续使用';
  formState.details = row?.details || '已发布新版本，点击下方按钮即可下载。';
  formState.downloadUrl = row?.downloadUrl || '';
  formState.releaseNotes = row?.releaseNotes || '';
  formState.publishedAt = row?.publishedAt || '';
  formState.updaterUrl = row?.updaterUrl || '';
  formState.updaterSignature = row?.updaterSignature || '';
  formState.updaterPubkey = row?.updaterPubkey || '';
};

const onEdit = (row: Partial<VersionPolicyItem>) => {
  fillForm(row);
};

const openPublicDownloadPage = () => {
  window.open(publicDownloadPageUrl.value, '_blank');
};

const onReset = () => {
  fillForm();
};

const onSave = async () => {
  if (!formState.latestVersion.trim()) {
    message.error('请先填写最新版本号');
    return;
  }
  saving.value = true;
  try {
    await saveVersionPolicyApi({ ...formState });
    message.success('版本下载配置已保存');
    await loadRows();
  } finally {
    saving.value = false;
  }
};

const copyDownloadPageUrl = async () => {
  try {
    await navigator.clipboard.writeText(publicDownloadPageUrl.value);
    message.success('公开下载页地址已复制');
  } catch {
    message.error('复制失败，请手动复制');
  }
};

onMounted(async () => {
  await loadRows();
  if (rows.value.length > 0) {
    fillForm(rows.value[0]);
  }
});
</script>

<template>
  <div class="version-policy-page">
    <ACard title="版本下载配置" :bordered="false" class="page-card">
      <AForm layout="vertical">
        <div class="grid two">
          <AFormItem label="应用代码">
            <AInput v-model:value="formState.appCode" placeholder="AiceMind" />
          </AFormItem>
          <AFormItem label="目标">
            <AInput v-model:value="formState.target" placeholder="backtest-desktop" />
          </AFormItem>
        </div>

        <div class="grid two">
          <AFormItem label="平台">
            <ASelect
              v-model:value="formState.platform"
              :options="[
                { label: 'all', value: 'all' },
                { label: 'windows', value: 'windows' },
                { label: 'macos', value: 'macos' },
              ]"
            />
          </AFormItem>
          <AFormItem label="渠道">
            <ASelect
              v-model:value="formState.channel"
              :options="[
                { label: 'stable', value: 'stable' },
                { label: 'beta', value: 'beta' },
              ]"
            />
          </AFormItem>
        </div>

        <div class="grid two">
          <AFormItem label="最新版本" required>
            <AInput v-model:value="formState.latestVersion" placeholder="例如：1.0.8" />
          </AFormItem>
          <AFormItem label="最低支持版本">
            <AInput v-model:value="formState.minSupportedVersion" placeholder="例如：1.0.6" />
          </AFormItem>
        </div>

        <AFormItem label="下载地址" required>
          <AInput v-model:value="formState.downloadUrl" placeholder="填写安装包直链，用户点击后直接下载" />
        </AFormItem>

        <AFormItem label="公开下载页地址">
          <div class="public-url-row">
            <AInput :value="publicDownloadPageUrl" readonly />
            <AButton @click="copyDownloadPageUrl">复制地址</AButton>
            <AButton type="primary" @click="openPublicDownloadPage">打开页面</AButton>
          </div>
        </AFormItem>

        <AFormItem label="下载提示标题">
          <AInput v-model:value="formState.title" placeholder="发现新版本，请升级后继续使用" />
        </AFormItem>

        <AFormItem label="下载提示说明">
          <AInput.TextArea v-model:value="formState.details" :rows="3" placeholder="展示在公开下载页顶部" />
        </AFormItem>

        <AFormItem label="更新说明">
          <AInput.TextArea v-model:value="formState.releaseNotes" :rows="6" placeholder="展示在公开下载页下方" />
        </AFormItem>

        <AFormItem label="发布时间">
          <AInput v-model:value="formState.publishedAt" placeholder="例如：2026-06-15 17:30" />
        </AFormItem>

        <AFormItem label="OTA 安装包地址（Updater URL）">
          <AInput v-model:value="formState.updaterUrl" placeholder="填写 OTA 安装包直链，例如 .app.tar.gz / .msi.zip" />
        </AFormItem>

        <AFormItem label="OTA 签名（Updater Signature）">
          <AInput.TextArea v-model:value="formState.updaterSignature" :rows="3" placeholder="填写 updater 产物签名内容" />
        </AFormItem>

        <AFormItem label="OTA 公钥（Updater Pubkey）">
          <AInput.TextArea v-model:value="formState.updaterPubkey" :rows="3" placeholder="填写 updater 公钥；客户端 tauri.conf 也需要同步" />
        </AFormItem>

        <AFormItem label="升级策略">
          <div class="switch-row">
            <div class="switch-item"><span>强制升级</span><ASwitch v-model:checked="formState.forceUpgrade" /></div>
            <div class="switch-item"><span>精确版本匹配</span><ASwitch v-model:checked="formState.enforceExactMatch" /></div>
            <div class="switch-item"><span>自动升级无需确认</span><ASwitch v-model:checked="formState.autoUpgradeWithoutConfirm" /></div>
          </div>
        </AFormItem>

        <AFormItem>
          <div class="action-row">
            <AButton @click="onReset">重置</AButton>
            <AButton type="primary" :loading="saving" @click="onSave">保存配置</AButton>
          </div>
        </AFormItem>
      </AForm>
    </ACard>

    <ACard title="已有版本策略" :bordered="false" class="page-card">
      <ATable :columns="columns" :data-source="rows" :loading="loading" :pagination="false" row-key="id">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'downloadUrl'">
            <a v-if="record.downloadUrl" :href="record.downloadUrl" target="_blank">{{ record.downloadUrl }}</a>
            <ATag v-else color="default">未配置</ATag>
          </template>
          <template v-else-if="column.key === 'action'">
            <AButton type="link" @click="onEdit(record)">编辑</AButton>
          </template>
        </template>
      </ATable>
    </ACard>
  </div>
</template>

<style scoped>
.version-policy-page {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-card {
  border-radius: 16px;
}

.grid.two {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.switch-row {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
}

.switch-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.action-row,
.public-url-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

@media (max-width: 768px) {
  .grid.two {
    grid-template-columns: 1fr;
  }
}
</style>
