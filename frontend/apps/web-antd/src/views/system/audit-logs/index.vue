<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';

import { listAuditLogsApi, type AuditLogItem } from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Space as ASpace,
  Table as ATable,
} from 'ant-design-vue';

const loading = ref(false);
const logs = ref<AuditLogItem[]>([]);

const filters = reactive({
  action: '',
  actor: '',
});

const loadLogs = async () => {
  loading.value = true;
  try {
    logs.value =
      (await listAuditLogsApi({
        action: filters.action || undefined,
        actor: filters.actor || undefined,
        limit: 300,
        offset: 0,
      })) || [];
  } finally {
    loading.value = false;
  }
};

onMounted(() => {
  void loadLogs();
});
</script>

<template>
  <div class="audit-logs-page">
    <ACard title="审计日志" :bordered="false">
      <AForm layout="inline" style="margin-bottom: 12px">
        <AFormItem label="动作">
          <AInput v-model:value="filters.action" placeholder="如 member.update" style="width: 220px" />
        </AFormItem>
        <AFormItem label="操作者ID">
          <AInput v-model:value="filters.actor" placeholder="账号ID" style="width: 220px" />
        </AFormItem>
        <AFormItem>
          <ASpace>
            <AButton type="primary" @click="loadLogs">查询</AButton>
            <AButton @click="filters.action = ''; filters.actor = ''; loadLogs()">重置</AButton>
          </ASpace>
        </AFormItem>
      </AForm>

      <ATable :data-source="logs" :loading="loading" row-key="id" :pagination="{ pageSize: 20 }">
        <ATable.Column title="时间" data-index="createdAt" key="createdAt" width="180" />
        <ATable.Column title="动作" data-index="action" key="action" width="220" />
        <ATable.Column title="操作者" data-index="actorAccountId" key="actorAccountId" width="220" />
        <ATable.Column title="目标" key="target">
          <template #default="{ record }">
            {{ record.targetType || '-' }} / {{ record.targetId || '-' }}
          </template>
        </ATable.Column>
        <ATable.Column title="详情" data-index="detail" key="detail" />
      </ATable>
    </ACard>
  </div>
</template>

<style scoped>
.audit-logs-page {
  padding: 16px;
}
</style>
