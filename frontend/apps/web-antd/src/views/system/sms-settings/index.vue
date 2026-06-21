<script lang="ts" setup>
import { onMounted, reactive, ref } from 'vue';

import { requestClient } from '#/api/request';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  message,
  Switch as ASwitch,
} from 'ant-design-vue';

interface SmsSettings {
  apiHost: string;
  account: string;
  password: string;
  signature: string;
  loginTemplateId: string;
  registerTemplateId: string;
  passwordTemplateId: string;
  report: boolean;
  callbackUrl: string;
  senderUid: string;
  timeoutSeconds: number;
}

const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const testPhone = ref('');

const formState = reactive<SmsSettings>({
  apiHost: 'https://smssh.253.com',
  account: '',
  password: '',
  signature: '',
  loginTemplateId: '',
  registerTemplateId: '',
  passwordTemplateId: '',
  report: true,
  callbackUrl: '',
  senderUid: '',
  timeoutSeconds: 20,
});

const loadSettings = async () => {
  loading.value = true;
  try {
    const data = await requestClient.get<SmsSettings>('/system/sms-settings');
    Object.assign(formState, data || {});
    if (!formState.apiHost) {
      formState.apiHost = 'https://smssh.253.com';
    }
    if (!formState.timeoutSeconds || formState.timeoutSeconds < 5) {
      formState.timeoutSeconds = 20;
    }
  } finally {
    loading.value = false;
  }
};

const onSave = async () => {
  saving.value = true;
  try {
    await requestClient.post('/system/sms-settings/save', {
      ...formState,
    });
    message.success('短信设置已保存');
  } finally {
    saving.value = false;
  }
};

const onSendTestSms = async () => {
  const phone = testPhone.value.trim();
  if (!phone) {
    message.error('请先输入测试手机号');
    return;
  }

  testing.value = true;
  try {
    await requestClient.post('/system/sms-settings/send-test', {
      ...formState,
      phone,
    });
    message.success(`测试短信已发送到 ${phone}`);
  } finally {
    testing.value = false;
  }
};

onMounted(() => {
  void loadSettings();
});
</script>

<template>
  <div class="sms-settings-page">
    <ACard :loading="loading" title="短信设置" :bordered="false">
      <AForm layout="vertical">
        <AFormItem label="API Host" required>
          <AInput v-model:value="formState.apiHost" placeholder="例如：https://smssh.253.com" />
        </AFormItem>

        <AFormItem label="创蓝账号" required>
          <AInput v-model:value="formState.account" placeholder="请输入短信平台 account" />
        </AFormItem>

        <AFormItem label="接口密码" required>
          <AInput
            v-model:value="formState.password"
            type="password"
            placeholder="请输入短信平台 password"
          />
        </AFormItem>

        <AFormItem label="短信签名">
          <AInput v-model:value="formState.signature" placeholder="例如：【AiceMind】" />
        </AFormItem>

        <AFormItem label="登录验证码模板 ID" required>
          <AInput v-model:value="formState.loginTemplateId" placeholder="请输入登录模板 ID" />
        </AFormItem>

        <AFormItem label="注册验证码模板 ID" required>
          <AInput v-model:value="formState.registerTemplateId" placeholder="请输入注册模板 ID" />
        </AFormItem>

        <AFormItem label="重置密码模板 ID">
          <AInput v-model:value="formState.passwordTemplateId" placeholder="如暂未使用可留空" />
        </AFormItem>

        <AFormItem label="回执与追踪">
          <div class="switch-row">
            <div class="switch-item">
              <span>开启状态回执</span>
              <ASwitch v-model:checked="formState.report" />
            </div>
          </div>
        </AFormItem>

        <AFormItem label="回调地址">
          <AInput v-model:value="formState.callbackUrl" placeholder="例如：https://your-domain.com/sms/callback" />
        </AFormItem>

        <AFormItem label="业务追踪 UID">
          <AInput v-model:value="formState.senderUid" placeholder="可选，用于请求追踪" />
        </AFormItem>

        <AFormItem label="请求超时（秒）">
          <AInputNumber
            v-model:value="formState.timeoutSeconds"
            :min="5"
            :max="120"
            style="width: 100%"
          />
        </AFormItem>

        <AFormItem>
          <div class="tips-box">
            <div>最小可用配置：API Host、账号、密码、登录模板 ID、注册模板 ID。</div>
            <div>测试短信会使用“登录验证码模板”向测试手机号发送固定验证码 123456。</div>
          </div>
        </AFormItem>

        <AFormItem>
          <div class="action-row">
            <AButton type="primary" :loading="saving" @click="onSave">
              保存设置
            </AButton>

            <div class="test-sms-row">
              <AInput
                v-model:value="testPhone"
                placeholder="输入测试手机号，例如 13800138000"
                style="width: 320px"
              />
              <AButton :loading="testing" @click="onSendTestSms">
                发送测试短信
              </AButton>
            </div>
          </div>
        </AFormItem>
      </AForm>
    </ACard>
  </div>
</template>

<style scoped>
.sms-settings-page {
  padding: 16px;
}

.switch-row {
  display: flex;
  gap: 24px;
}

.switch-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tips-box {
  padding: 12px 14px;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  color: #475569;
  display: grid;
  gap: 6px;
  font-size: 13px;
}

.action-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.test-sms-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
