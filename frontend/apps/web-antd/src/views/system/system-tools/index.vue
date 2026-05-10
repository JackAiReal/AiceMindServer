<script setup lang="ts">
import { ref } from 'vue';

import { exportConfigApi, importConfigApi } from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  message,
  Space as ASpace,
  Upload as AUpload,
} from 'ant-design-vue';

const exporting = ref(false);
const importing = ref(false);
const importFileList = ref<any[]>([]);

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
</script>

<template>
  <div class="system-tools-page">
    <ASpace direction="vertical" :size="16" style="width: 100%">
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
          <li><b>导出配置</b>：将当前系统的所有配置数据打包为 JSON 文件下载到本地</li>
          <li><b>导入配置</b>：选择之前导出的 JSON 文件，一键恢复所有配置到新环境</li>
          <li>导出内容包含：安全策略、邮箱设置、支付设置、观测告警、合规文档、套餐计划</li>
          <li>导入时会覆盖目标环境的现有配置，请谨慎操作</li>
          <li>业务数据（订单、订阅、用户等）不会被导出/导入</li>
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
