<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';

import {
  getSecurityPolicyApi,
  listLoginAttemptsApi,
  listLoginRiskEventsApi,
  listSecuritySessionsApi,
  revokeAccountSessionsApi,
  revokeSecuritySessionApi,
  saveSecurityPolicyApi,
  unlockLoginAttemptApi,
  type LoginAttemptItem,
  type LoginRiskEventItem,
  type SecurityPolicy,
  type SecuritySessionItem,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  InputNumber as AInputNumber,
  message,
  Modal,
  Popconfirm as APopconfirm,
  Space as ASpace,
  Switch as ASwitch,
  Table as ATable,
  Tag as ATag,
} from 'ant-design-vue';

const loadingSessions = ref(false);
const loadingAttempts = ref(false);
const savingPolicy = ref(false);
const sessions = ref<SecuritySessionItem[]>([]);
const attempts = ref<LoginAttemptItem[]>([]);
const riskEvents = ref<LoginRiskEventItem[]>([]);

const policy = reactive<SecurityPolicy>({
  passwordMinLength: 8,
  passwordRequireLetter: true,
  passwordRequireDigit: true,
  passwordRequireSpecial: false,
  loginFailMax: 5,
  loginFailWindowMinutes: 15,
  loginLockMinutes: 15,
  sessionTtlHours: 24,
  forceLogoutOnPasswordReset: true,
});

const loadSessions = async () => {
  loadingSessions.value = true;
  try {
    sessions.value = (await listSecuritySessionsApi()) || [];
  } finally {
    loadingSessions.value = false;
  }
};

const loadAttempts = async () => {
  loadingAttempts.value = true;
  try {
    attempts.value = (await listLoginAttemptsApi()) || [];
  } finally {
    loadingAttempts.value = false;
  }
};

const loadPolicy = async () => {
  const data = await getSecurityPolicyApi();
  Object.assign(policy, data || {});
};

const loadRiskEvents = async () => {
  riskEvents.value = (await listLoginRiskEventsApi({ limit: 200 })) || [];
};

const refresh = async () => {
  await Promise.all([loadSessions(), loadAttempts(), loadPolicy(), loadRiskEvents()]);
};

const onSavePolicy = async () => {
  savingPolicy.value = true;
  try {
    await saveSecurityPolicyApi({
      ...policy,
      passwordMinLength: Number(policy.passwordMinLength || 8),
      loginFailMax: Number(policy.loginFailMax || 5),
      loginFailWindowMinutes: Number(policy.loginFailWindowMinutes || 15),
      loginLockMinutes: Number(policy.loginLockMinutes || 15),
      sessionTtlHours: Number(policy.sessionTtlHours || 24),
      forceLogoutOnPasswordReset: !!policy.forceLogoutOnPasswordReset,
    });
    message.success('安全策略已保存');
    await loadPolicy();
  } finally {
    savingPolicy.value = false;
  }
};

const onRevoke = (row: SecuritySessionItem) => {
  Modal.confirm({
    title: '确认撤销该会话？',
    content: `${row.username || row.accountId}（${row.id}）`,
    okText: '撤销',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      await revokeSecuritySessionApi(row.id);
      message.success('会话已撤销');
      await loadSessions();
    },
  });
};

const onRevokeAccount = (row: SecuritySessionItem) => {
  Modal.confirm({
    title: '确认撤销该账号全部会话？',
    content: `${row.username || row.accountId}`,
    okText: '确认撤销',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      await revokeAccountSessionsApi(row.accountId);
      message.success('该账号会话已全部撤销');
      await loadSessions();
    },
  });
};

const onUnlock = async (row: LoginAttemptItem) => {
  await unlockLoginAttemptApi(row.loginKey);
  message.success('该登录标识限制已解除');
  await loadAttempts();
};

onMounted(() => {
  void refresh();
});
</script>

<template>
  <div class="security-center-page">
    <ASpace direction="vertical" :size="16" style="width: 100%">
      <ACard title="账户安全基线" :bordered="false">
        <template #extra>
          <AButton @click="refresh">刷新</AButton>
        </template>
        <AForm layout="inline">
          <AFormItem label="密码最小长度">
            <AInputNumber v-model:value="policy.passwordMinLength" :min="6" :max="64" />
          </AFormItem>
          <AFormItem label="需字母"><ASwitch v-model:checked="policy.passwordRequireLetter" /></AFormItem>
          <AFormItem label="需数字"><ASwitch v-model:checked="policy.passwordRequireDigit" /></AFormItem>
          <AFormItem label="需特殊字符"><ASwitch v-model:checked="policy.passwordRequireSpecial" /></AFormItem>
          <AFormItem label="失败阈值">
            <AInputNumber v-model:value="policy.loginFailMax" :min="3" :max="20" />
          </AFormItem>
          <AFormItem label="统计窗口(分钟)">
            <AInputNumber v-model:value="policy.loginFailWindowMinutes" :min="1" :max="120" />
          </AFormItem>
          <AFormItem label="锁定时长(分钟)">
            <AInputNumber v-model:value="policy.loginLockMinutes" :min="1" :max="240" />
          </AFormItem>
          <AFormItem label="会话TTL(小时)">
            <AInputNumber v-model:value="policy.sessionTtlHours" :min="1" :max="168" />
          </AFormItem>
          <AFormItem label="重置密码后强制下线">
            <ASwitch v-model:checked="policy.forceLogoutOnPasswordReset" />
          </AFormItem>
          <AFormItem>
            <AButton type="primary" :loading="savingPolicy" @click="onSavePolicy">保存基线</AButton>
          </AFormItem>
        </AForm>
      </ACard>

      <ACard title="会话安全中心" :bordered="false">
        <ATable :data-source="sessions" :loading="loadingSessions" row-key="id" :pagination="{ pageSize: 10 }">
          <ATable.Column title="账号" key="account">
            <template #default="{ record }">
              <div>{{ record.username || '-' }}</div>
              <div style="font-size: 12px; color: #888">{{ record.email || record.accountId }}</div>
            </template>
          </ATable.Column>
          <ATable.Column title="最近活跃" data-index="lastActiveAt" key="lastActiveAt" />
          <ATable.Column title="过期时间" data-index="expireAt" key="expireAt" />
          <ATable.Column title="状态" key="status">
            <template #default="{ record }">
              <ATag v-if="record.isCurrent" color="blue">当前会话</ATag>
              <ATag v-else-if="record.isRevoked" color="red">已撤销</ATag>
              <ATag v-else-if="record.isExpired" color="orange">已过期</ATag>
              <ATag v-else color="green">有效</ATag>
            </template>
          </ATable.Column>
          <ATable.Column title="操作" key="actions" width="220">
            <template #default="{ record }">
              <ASpace>
                <AButton
                  danger
                  size="small"
                  :disabled="record.isRevoked || record.isCurrent"
                  @click="onRevoke(record)"
                >
                  撤销当前会话
                </AButton>
                <AButton danger type="link" size="small" @click="onRevokeAccount(record)">
                  撤销账号全部会话
                </AButton>
              </ASpace>
            </template>
          </ATable.Column>
        </ATable>
      </ACard>

      <ACard title="登录失败监控" :bordered="false">
        <ATable :data-source="attempts" :loading="loadingAttempts" row-key="loginKey" :pagination="{ pageSize: 10 }">
          <ATable.Column title="登录标识" data-index="loginKey" key="loginKey" />
          <ATable.Column title="失败次数" data-index="failCount" key="failCount" />
          <ATable.Column title="首次失败" data-index="firstFailAt" key="firstFailAt" />
          <ATable.Column title="锁定至" data-index="lockedUntil" key="lockedUntil" />
          <ATable.Column title="更新时间" data-index="updatedAt" key="updatedAt" />
          <ATable.Column title="操作" key="actions" width="100">
            <template #default="{ record }">
              <APopconfirm title="确认解除该登录限制？" ok-text="确认" cancel-text="取消" @confirm="onUnlock(record)">
                <AButton type="link" size="small">解除限制</AButton>
              </APopconfirm>
            </template>
          </ATable.Column>
        </ATable>
      </ACard>

      <ACard title="异地/新设备登录风险事件" :bordered="false">
        <ATable :data-source="riskEvents" row-key="id" :pagination="{ pageSize: 10 }">
          <ATable.Column title="时间" data-index="createdAt" key="createdAt" width="180" />
          <ATable.Column title="账号" key="account" width="220">
            <template #default="{ record }">
              <div>{{ record.username || '-' }}</div>
              <div style="font-size: 12px; color: #888">{{ record.accountId || '-' }}</div>
            </template>
          </ATable.Column>
          <ATable.Column title="IP" data-index="loginIp" key="loginIp" width="170" />
          <ATable.Column title="设备" data-index="userAgent" key="userAgent" width="260" />
          <ATable.Column title="风险等级" key="riskLevel" width="100">
            <template #default="{ record }">
              <ATag :color="record.riskLevel === 'high' ? 'red' : 'green'">{{ record.riskLevel || '-' }}</ATag>
            </template>
          </ATable.Column>
          <ATable.Column title="风险原因" data-index="riskReason" key="riskReason" />
          <ATable.Column title="已通知" key="notified" width="100">
            <template #default="{ record }">{{ record.notified ? '是' : '否' }}</template>
          </ATable.Column>
        </ATable>
      </ACard>
    </ASpace>
  </div>
</template>

<style scoped>
.security-center-page {
  padding: 16px;
}
</style>
