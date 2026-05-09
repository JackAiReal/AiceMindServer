<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';

import {
  getObservabilitySettingsApi,
  listErrorEventsApi,
  listRequestMetricsApi,
  saveObservabilitySettingsApi,
  testObservabilityAlertApi,
  type ErrorEventItem,
  type ObservabilitySettings,
  type RequestMetricsResult,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  message,
  Space as ASpace,
  Statistic as AStatistic,
  Table as ATable,
} from 'ant-design-vue';

const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const minutes = ref(60);

const settings = reactive<ObservabilitySettings>({
  sentryDsn: '',
  alertWebhook: '',
  alertEmails: '',
});

const metrics = ref<RequestMetricsResult | null>(null);
const errors = ref<ErrorEventItem[]>([]);

const loadData = async () => {
  loading.value = true;
  try {
    const [settingData, metricData, errorRows] = await Promise.all([
      getObservabilitySettingsApi(),
      listRequestMetricsApi({ minutes: Number(minutes.value || 60), limit: 500 }),
      listErrorEventsApi({ limit: 300 }),
    ]);

    Object.assign(settings, settingData || {});
    metrics.value = metricData || null;
    errors.value = errorRows || [];
  } finally {
    loading.value = false;
  }
};

const onSave = async () => {
  saving.value = true;
  try {
    await saveObservabilitySettingsApi({ ...settings });
    message.success('观测配置已保存');
  } finally {
    saving.value = false;
  }
};

const onTestAlert = async () => {
  testing.value = true;
  try {
    await testObservabilityAlertApi();
    message.success('测试告警已触发');
    await loadData();
  } finally {
    testing.value = false;
  }
};

onMounted(() => {
  void loadData();
});
</script>

<template>
  <div class="observability-page">
    <ASpace direction="vertical" :size="16" style="width: 100%">
      <ACard title="观测与告警配置" :bordered="false" :loading="loading">
        <template #extra>
          <ASpace>
            <AButton @click="loadData">刷新</AButton>
            <AButton :loading="testing" @click="onTestAlert">测试告警</AButton>
            <AButton type="primary" :loading="saving" @click="onSave">保存配置</AButton>
          </ASpace>
        </template>

        <AForm layout="vertical">
          <AFormItem label="Sentry DSN（可选）">
            <AInput v-model:value="settings.sentryDsn" placeholder="https://xxx.ingest.sentry.io/xxxx" />
          </AFormItem>
          <AFormItem label="告警 Webhook">
            <AInput v-model:value="settings.alertWebhook" placeholder="https://hooks.xxx.com/observability" />
          </AFormItem>
          <AFormItem label="告警邮箱（逗号/分号分隔）">
            <AInput v-model:value="settings.alertEmails" placeholder="ops@your.com,admin@your.com" />
          </AFormItem>
        </AForm>
      </ACard>

      <ACard title="接口指标概览" :bordered="false" :loading="loading">
        <template #extra>
          <ASpace>
            <span>统计窗口(分钟)：</span>
            <AInputNumber v-model:value="minutes" :min="1" :max="1440" style="width: 96px" />
            <AButton type="primary" @click="loadData">更新</AButton>
          </ASpace>
        </template>

        <ASpace size="20" wrap>
          <AStatistic title="请求总数" :value="metrics?.summary.total || 0" />
          <AStatistic title="成功率" :value="((metrics?.summary.successRate || 0) * 100).toFixed(2) + '%'" />
          <AStatistic title="平均延迟(ms)" :value="metrics?.summary.avgLatencyMs || 0" />
          <AStatistic title="最大延迟(ms)" :value="metrics?.summary.maxLatencyMs || 0" />
          <AStatistic title="5xx 数量" :value="metrics?.summary.serverErrorCount || 0" />
        </ASpace>

        <ATable
          style="margin-top: 14px"
          :data-source="metrics?.items || []"
          row-key="createdAt"
          :pagination="{ pageSize: 10 }"
        >
          <ATable.Column title="时间" data-index="createdAt" key="createdAt" width="180" />
          <ATable.Column title="方法" data-index="method" key="method" width="90" />
          <ATable.Column title="路径" data-index="path" key="path" width="340" />
          <ATable.Column title="状态码" data-index="statusCode" key="statusCode" width="100" />
          <ATable.Column title="成功" key="success" width="90">
            <template #default="{ record }">{{ record.success ? '是' : '否' }}</template>
          </ATable.Column>
          <ATable.Column title="延迟(ms)" data-index="latencyMs" key="latencyMs" />
        </ATable>
      </ACard>

      <ACard title="错误事件" :bordered="false" :loading="loading">
        <ATable :data-source="errors" row-key="id" :pagination="{ pageSize: 10 }">
          <ATable.Column title="时间" data-index="createdAt" key="createdAt" width="180" />
          <ATable.Column title="来源" data-index="source" key="source" width="140" />
          <ATable.Column title="级别" data-index="level" key="level" width="100" />
          <ATable.Column title="路径" data-index="path" key="path" width="260" />
          <ATable.Column title="消息" data-index="message" key="message" width="260" />
          <ATable.Column title="详情" data-index="detail" key="detail" />
        </ATable>
      </ACard>
    </ASpace>
  </div>
</template>

<style scoped>
.observability-page {
  padding: 16px;
}
</style>
