<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';

import {
  exportConfigApi,
  importConfigApi,
  listVersionPoliciesApi,
  saveVersionPolicyApi,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  message,
  Select as ASelect,
  Space as ASpace,
  Switch as ASwitch,
  Upload as AUpload,
} from 'ant-design-vue';

const exporting = ref(false);
const importing = ref(false);
const savingVersion = ref(false);
const importFileList = ref<any[]>([]);

const versionForm = reactive({
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
  details: '',
  downloadUrl: '',
  releaseNotes: '',
  publishedAt: '',
});

const loadVersionPolicy = async () => {
  try {
    const rows = await listVersionPoliciesApi();
    const hit = (rows || []).find(
      (x) =>
        x.appCode === versionForm.appCode
        && x.target === versionForm.target
        && x.platform === versionForm.platform
        && x.channel === versionForm.channel,
    );
    if (hit) {
      Object.assign(versionForm, {
        appCode: hit.appCode,
        target: hit.target,
        platform: hit.platform,
        channel: hit.channel,
        latestVersion: hit.latestVersion || '',
        minSupportedVersion: hit.minSupportedVersion || '',
        enforceExactMatch: !!hit.enforceExactMatch,
        forceUpgrade: !!hit.forceUpgrade,
        autoUpgradeWithoutConfirm: !!hit.autoUpgradeWithoutConfirm,
        title: hit.title || '发现新版本，请升级后继续使用',
        details: hit.details || '',
        downloadUrl: hit.downloadUrl || '',
        releaseNotes: hit.releaseNotes || '',
        publishedAt: hit.publishedAt || '',
      });
    }
  } catch {
    // ignore
  }
};

const saveVersionPolicy = async () => {
  if (!versionForm.latestVersion.trim()) {
    message.error('请填写最新版本号');
    return;
  }

  savingVersion.value = true;
  try {
    await saveVersionPolicyApi({ ...versionForm });
    message.success('版本策略已保存');
  } catch (e: any) {
    message.error(e?.response?.data?.message || e?.message || '保存失败');
  } finally {
    savingVersion.value = false;
  }
};

const onExport = async () => {
  exporting.value = true;
  try {
    const payload = await exportConfigApi();

    const blob =
      payload instanceof Blob
        ? payload
        : new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });

    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `aicemind-config-${new Date().toISOString().slice(0, 19).replace(/:/g, '')}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
    message.success('配置导出成功');
  } catch (e) {
    message.error('配置导出失败');
  } finally {
    exporting.value = false;
  }
};

const beforeUpload = (file: File) => {
  if (!file.name.endsWith('.json')) {
    message.error('请上传 .json 文件');
    return false;
  }
  handleImport(file);
  return false;
};

const handleImport = async (file: File) => {
  importing.value = true;
  try {
    const res = await importConfigApi(file);
    const results = res.data?.results || {};
    const okCount = Object.values(results).filter((v) => String(v).startsWith('ok')).length;
    message.success(`配置导入完成，${okCount} 个表导入成功`);
    importFileList.value = [];
  } catch (e: any) {
    const msg = e?.response?.data?.message || e?.message || '导入失败';
    message.error(msg);
  } finally {
    importing.value = false;
  }
};

onMounted(() => {
  loadVersionPolicy();
});
</script>

<template>
  <div class="system-tools-page">
    <ASpace direction="vertical" :size="16" style="width: 100%">
      <ACard title="客户端版本管控（桌面端）" :bordered="false">
        <p style="color: #666; margin-bottom: 12px">
          控制桌面端是否必须升级后才能运行，可配置强制升级与“无确认自动升级”。
        </p>

        <AForm layout="vertical">
          <div style="display: grid; grid-template-columns: repeat(4, minmax(180px, 1fr)); gap: 12px">
            <AFormItem label="应用代码">
              <AInput v-model:value="versionForm.appCode" placeholder="AiceMind" />
            </AFormItem>
            <AFormItem label="目标端">
              <AInput v-model:value="versionForm.target" placeholder="backtest-desktop" />
            </AFormItem>
            <AFormItem label="平台">
              <ASelect
                v-model:value="versionForm.platform"
                :options="[
                  { label: 'all', value: 'all' },
                  { label: 'windows', value: 'windows' },
                  { label: 'macos', value: 'macos' },
                  { label: 'linux', value: 'linux' },
                ]"
              />
            </AFormItem>
            <AFormItem label="渠道">
              <ASelect
                v-model:value="versionForm.channel"
                :options="[
                  { label: 'stable', value: 'stable' },
                  { label: 'beta', value: 'beta' },
                  { label: 'canary', value: 'canary' },
                ]"
              />
            </AFormItem>
          </div>

          <div style="display: grid; grid-template-columns: repeat(2, minmax(260px, 1fr)); gap: 12px">
            <AFormItem label="最新版本（latestVersion）">
              <AInput v-model:value="versionForm.latestVersion" placeholder="如 1.2.3" />
            </AFormItem>
            <AFormItem label="最低兼容版本（minSupportedVersion，可选）">
              <AInput v-model:value="versionForm.minSupportedVersion" placeholder="如 1.2.0" />
            </AFormItem>
          </div>

          <div style="display: grid; grid-template-columns: repeat(3, minmax(220px, 1fr)); gap: 12px; margin-bottom: 8px">
            <AFormItem label="严格一致才可运行">
              <ASwitch v-model:checked="versionForm.enforceExactMatch" />
            </AFormItem>
            <AFormItem label="不满足规则时强制升级">
              <ASwitch v-model:checked="versionForm.forceUpgrade" />
            </AFormItem>
            <AFormItem label="升级时免确认自动执行">
              <ASwitch v-model:checked="versionForm.autoUpgradeWithoutConfirm" />
            </AFormItem>
          </div>

          <AFormItem label="升级提示标题">
            <AInput v-model:value="versionForm.title" placeholder="发现新版本，请升级后继续使用" />
          </AFormItem>
          <AFormItem label="升级详情（展示给客户端）">
            <AInput.TextArea
              v-model:value="versionForm.details"
              :rows="4"
              placeholder="例如：修复回测崩溃问题、优化策略校验、提升稳定性..."
            />
          </AFormItem>
          <AFormItem label="下载地址">
            <AInput v-model:value="versionForm.downloadUrl" placeholder="https://..." />
          </AFormItem>
          <AFormItem label="发布说明 / Changelog">
            <AInput.TextArea v-model:value="versionForm.releaseNotes" :rows="3" placeholder="可选" />
          </AFormItem>
          <AFormItem label="发布时间（可选）">
            <AInput v-model:value="versionForm.publishedAt" placeholder="YYYY-MM-DD HH:mm:ss" />
          </AFormItem>

          <ASpace>
            <AButton :loading="savingVersion" type="primary" @click="saveVersionPolicy">保存版本策略</AButton>
            <AButton @click="loadVersionPolicy">刷新当前策略</AButton>
          </ASpace>
        </AForm>
      </ACard>

      <ACard title="配置迁移工具" :bordered="false">
        <p style="color: #666; margin-bottom: 16px">
          一键导出当前系统的所有配置（支付设置、邮件设置、安全策略、合规文档、套餐等），<br />
          在新环境部署后可一键导入，快速恢复配置。
        </p>

        <ASpace size="middle">
          <AButton type="primary" :loading="exporting" @click="onExport">
            <template #icon>
              <span class="iconify" data-icon="mdi:download"></span>
            </template>
            导出配置
          </AButton>

          <AUpload
            v-model:file-list="importFileList"
            :before-upload="beforeUpload"
            :show-upload-list="false"
            accept=".json"
          >
            <AButton :loading="importing">
              <template #icon>
                <span class="iconify" data-icon="mdi:upload"></span>
              </template>
              导入配置
            </AButton>
          </AUpload>
        </ASpace>
      </ACard>

      <ACard title="说明" :bordered="false">
        <ul style="color: #666; line-height: 2">
          <li><b>版本管控</b>：桌面端启动时可先调用版本检查接口，不满足规则则提示升级</li>
          <li><b>严格一致</b>：开启后仅当客户端版本 = latestVersion 才允许继续运行</li>
          <li><b>免确认自动升级</b>：仅下发策略，不直接替客户端执行；客户端收到后可自动安装</li>
          <li><b>导出配置</b>：将当前系统配置打包为 JSON 文件下载到本地</li>
          <li><b>导入配置</b>：选择之前导出的 JSON 文件，一键恢复配置到新环境</li>
        </ul>
      </ACard>
    </ASpace>
  </div>
</template>

<style scoped>
.system-tools-page {
  padding: 16px;
}
</style>
