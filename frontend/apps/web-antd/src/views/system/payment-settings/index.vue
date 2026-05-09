<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';

import {
  getPaymentSettingsApi,
  getPaymentTradeDetailApi,
  savePaymentSettingsApi,
  testPaymentApi,
  type PaymentSettings,
  type PaymentTestPayResult,
  type PaymentTradeItem,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  message,
  Modal as AModal,
  Radio as ARadio,
  Space as ASpace,
  Switch as ASwitch,
  Tag as ATag,
} from 'ant-design-vue';

const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const checkingTrade = ref(false);

const testModalOpen = ref(false);
const payModalOpen = ref(false);
const testProvider = ref<'alipay' | 'wechat'>('alipay');
const testResultText = ref('');

const testPayResult = ref<PaymentTestPayResult | null>(null);
const testTradeDetail = ref<PaymentTradeItem | null>(null);

let tradePollTimer: ReturnType<typeof setInterval> | null = null;

const formState = reactive<PaymentSettings>({
  alipayEnabled: false,
  alipayAppId: '',
  alipayMerchantId: '',
  alipayAppPrivateKey: '',
  alipayPublicKey: '',
  alipayGateway: 'https://openapi.alipay.com/gateway.do',
  alipayNotifyUrl: '',
  alipayReturnUrl: '',
  alipaySignType: 'RSA2',

  wechatEnabled: false,
  wechatAppId: '',
  wechatMerchantId: '',
  wechatApiV3Key: '',
  wechatPrivateKey: '',
  wechatSerialNo: '',
  wechatGateway: 'https://api.mch.weixin.qq.com',
  wechatNotifyUrl: '',
  wechatReturnUrl: '',

  paymentAlertEnabled: false,
  paymentAlertEmails: '',
  paymentAlertWebhook: '',
});

const qrImageUrl = computed(() => {
  const code = testPayResult.value?.qrCode || '';
  if (!code) return '';
  return `https://api.qrserver.com/v1/create-qr-code/?size=280x280&data=${encodeURIComponent(code)}`;
});

const tradePaid = computed(() => {
  const tradeStatus = (testTradeDetail.value?.status || '').toLowerCase();
  const orderStatus = (testTradeDetail.value?.orderStatus || '').toLowerCase();
  return tradeStatus === 'paid' || orderStatus === 'paid';
});

const loadSettings = async () => {
  loading.value = true;
  try {
    const data = await getPaymentSettingsApi();
    Object.assign(formState, data || {});
  } finally {
    loading.value = false;
  }
};

const onSave = async () => {
  saving.value = true;
  try {
    await savePaymentSettingsApi({ ...formState });
    message.success('支付设置已保存');
  } finally {
    saving.value = false;
  }
};

const clearTradePolling = () => {
  if (tradePollTimer) {
    clearInterval(tradePollTimer);
    tradePollTimer = null;
  }
};

const refreshTradeStatus = async () => {
  const tradeId = testPayResult.value?.tradeId;
  if (!tradeId) return;

  checkingTrade.value = true;
  try {
    const data = await getPaymentTradeDetailApi({ tradeId });
    testTradeDetail.value = data || null;

    if (tradePaid.value) {
      clearTradePolling();
      message.success(`支付成功：测试订单 ${testPayResult.value?.orderNo || ''} 已支付`);
    }
  } finally {
    checkingTrade.value = false;
  }
};

const startTradePolling = () => {
  clearTradePolling();
  tradePollTimer = setInterval(() => {
    if (!payModalOpen.value) {
      clearTradePolling();
      return;
    }
    void refreshTradeStatus();
  }, 5000);
};

const onOpenTestModal = () => {
  testProvider.value = 'alipay';
  testModalOpen.value = true;
};

const onConfirmTestPay = async () => {
  testing.value = true;
  try {
    const result = (await testPaymentApi({
      provider: testProvider.value,
      amount: 0.01,
      currency: 'CNY',
      description: '支付配置测试 0.01 元',
    })) as PaymentTestPayResult;

    testPayResult.value = result || null;
    testTradeDetail.value = null;
    testResultText.value = JSON.stringify(result || {}, null, 2);
    testModalOpen.value = false;

    if (result?.provider === 'alipay' && result?.qrCode) {
      payModalOpen.value = true;
      await refreshTradeStatus();
      startTradePolling();
      message.success('已生成真实支付宝二维码，请扫码支付 0.01 元');
    } else {
      message.success(`已生成 ${testProvider.value} 测试订单`);
    }
  } finally {
    testing.value = false;
  }
};

const onClosePayModal = () => {
  payModalOpen.value = false;
  clearTradePolling();
};

onMounted(() => {
  void loadSettings();
});

onBeforeUnmount(() => {
  clearTradePolling();
});
</script>

<template>
  <div class="payment-settings-page">
    <ASpace direction="vertical" :size="16" style="width: 100%">
      <ACard title="支付设置" :bordered="false" :loading="loading">
        <template #extra>
          <ASpace>
            <AButton @click="loadSettings">刷新</AButton>
            <AButton type="primary" :loading="saving" @click="onSave">保存支付配置</AButton>
          </ASpace>
        </template>

        <AForm layout="vertical">
          <div class="section-title">支付宝配置</div>
          <AFormItem label="启用支付宝">
            <ASwitch v-model:checked="formState.alipayEnabled" />
          </AFormItem>
          <AFormItem label="支付宝 AppId" required>
            <AInput v-model:value="formState.alipayAppId" placeholder="填写支付宝开放平台 AppId" />
          </AFormItem>
          <AFormItem label="支付宝商户号/合作伙伴号" required>
            <AInput v-model:value="formState.alipayMerchantId" placeholder="填写 merchantId / PID" />
          </AFormItem>
          <AFormItem label="应用私钥（RSA2）" required>
            <AInput.TextArea
              v-model:value="formState.alipayAppPrivateKey"
              :rows="4"
              placeholder="填写应用私钥（建议后续迁移到服务端密钥管理）"
            />
          </AFormItem>
          <AFormItem label="支付宝公钥" required>
            <AInput.TextArea
              v-model:value="formState.alipayPublicKey"
              :rows="4"
              placeholder="填写支付宝公钥"
            />
          </AFormItem>
          <AFormItem label="网关地址 Gateway">
            <AInput v-model:value="formState.alipayGateway" placeholder="https://openapi.alipay.com/gateway.do" />
          </AFormItem>
          <AFormItem label="异步通知地址 Notify URL" required>
            <AInput
              v-model:value="formState.alipayNotifyUrl"
              placeholder="http://公网地址/api/admin/payment/callback/alipay"
            />
          </AFormItem>
          <AFormItem label="同步回跳地址 Return URL">
            <AInput v-model:value="formState.alipayReturnUrl" placeholder="https://admin.yourdomain.com/payment/alipay/return" />
          </AFormItem>
          <AFormItem label="签名类型">
            <AInput v-model:value="formState.alipaySignType" placeholder="RSA2" />
          </AFormItem>

          <div class="section-title">微信支付配置</div>
          <AFormItem label="启用微信支付">
            <ASwitch v-model:checked="formState.wechatEnabled" />
          </AFormItem>
          <AFormItem label="微信 AppId" required>
            <AInput v-model:value="formState.wechatAppId" placeholder="填写微信 AppId" />
          </AFormItem>
          <AFormItem label="微信商户号 mchid" required>
            <AInput v-model:value="formState.wechatMerchantId" placeholder="填写微信商户号" />
          </AFormItem>
          <AFormItem label="APIv3 Key" required>
            <AInput.Password v-model:value="formState.wechatApiV3Key" placeholder="填写 APIv3 Key" />
          </AFormItem>
          <AFormItem label="商户私钥" required>
            <AInput.TextArea
              v-model:value="formState.wechatPrivateKey"
              :rows="4"
              placeholder="填写商户私钥 PEM"
            />
          </AFormItem>
          <AFormItem label="商户证书序列号 Serial No" required>
            <AInput v-model:value="formState.wechatSerialNo" placeholder="填写证书序列号" />
          </AFormItem>
          <AFormItem label="网关地址 Gateway">
            <AInput v-model:value="formState.wechatGateway" placeholder="https://api.mch.weixin.qq.com" />
          </AFormItem>
          <AFormItem label="异步通知地址 Notify URL">
            <AInput v-model:value="formState.wechatNotifyUrl" placeholder="https://api.yourdomain.com/payment/wechat/notify" />
          </AFormItem>
          <AFormItem label="回跳地址 Return URL（如有）">
            <AInput v-model:value="formState.wechatReturnUrl" placeholder="https://admin.yourdomain.com/payment/wechat/return" />
          </AFormItem>

          <div class="section-title">支付告警配置</div>
          <AFormItem label="启用支付告警">
            <ASwitch v-model:checked="formState.paymentAlertEnabled" />
          </AFormItem>
          <AFormItem label="告警邮箱（逗号/分号分隔）">
            <AInput v-model:value="formState.paymentAlertEmails" placeholder="ops@your.com,admin@your.com" />
          </AFormItem>
          <AFormItem label="告警 Webhook（可选）">
            <AInput v-model:value="formState.paymentAlertWebhook" placeholder="https://hooks.xxx.com/payment-alert" />
          </AFormItem>
        </AForm>
      </ACard>

      <ACard title="支付联调测试" :bordered="false">
        <ASpace>
          <AButton type="primary" :loading="testing" @click="onOpenTestModal">测试支付 0.01 元</AButton>
          <AButton @click="testResultText = ''">清空测试结果</AButton>
          <AButton v-if="testPayResult?.tradeId" :loading="checkingTrade" @click="refreshTradeStatus">刷新支付状态</AButton>
        </ASpace>

        <div class="hint">
          点击“测试支付 0.01 元”后，系统会创建测试订单；支付宝会弹出可扫码的真实二维码，支付成功后订单会自动变为 paid。
        </div>

        <AInput.TextArea
          v-model:value="testResultText"
          :rows="14"
          placeholder="测试结果会显示在这里（含 orderNo / tradeId / outTradeNo / qrCode）"
        />
      </ACard>
    </ASpace>

    <AModal
      v-model:open="testModalOpen"
      title="选择测试支付渠道"
      :confirm-loading="testing"
      ok-text="开始测试"
      cancel-text="取消"
      @ok="onConfirmTestPay"
    >
      <ARadio.Group v-model:value="testProvider">
        <ASpace direction="vertical">
          <ARadio value="alipay">测试支付宝（0.01 元，真实扫码）</ARadio>
          <ARadio value="wechat">测试微信支付（0.01 元）</ARadio>
        </ASpace>
      </ARadio.Group>
    </AModal>

    <AModal
      v-model:open="payModalOpen"
      title="支付宝测试支付二维码"
      width="560px"
      :footer="null"
      @cancel="onClosePayModal"
    >
      <div class="pay-modal">
        <div class="pay-order-info">
          <div><b>订单号：</b>{{ testPayResult?.orderNo || '-' }}</div>
          <div><b>交易号：</b>{{ testPayResult?.outTradeNo || '-' }}</div>
          <div><b>金额：</b>{{ testPayResult?.amount || 0 }} {{ testPayResult?.currency || 'CNY' }}</div>
          <div>
            <b>支付状态：</b>
            <ATag :color="tradePaid ? 'green' : 'orange'">
              {{ tradePaid ? '已支付' : (testTradeDetail?.status || '待支付') }}
            </ATag>
          </div>
        </div>

        <div class="pay-qr-wrap">
          <img v-if="qrImageUrl" :src="qrImageUrl" alt="支付宝支付二维码" class="pay-qr-img" />
          <div v-else class="pay-qr-empty">二维码生成中...</div>
        </div>

        <div class="pay-actions">
          <AButton :loading="checkingTrade" type="primary" @click="refreshTradeStatus">我已支付，立即检查</AButton>
          <AButton @click="onClosePayModal">关闭</AButton>
        </div>
      </div>
    </AModal>
  </div>
</template>

<style scoped>
.payment-settings-page {
  padding: 16px;
}

.section-title {
  font-weight: 600;
  margin: 8px 0;
  color: #1677ff;
}

.hint {
  margin: 10px 0;
  color: #666;
  font-size: 13px;
}

.pay-modal {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.pay-order-info {
  line-height: 1.8;
  font-size: 14px;
}

.pay-qr-wrap {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 300px;
  border: 1px dashed #ddd;
  border-radius: 8px;
  padding: 12px;
}

.pay-qr-img {
  width: 280px;
  height: 280px;
  object-fit: contain;
}

.pay-qr-empty {
  color: #999;
}

.pay-actions {
  display: flex;
  justify-content: center;
  gap: 12px;
}
</style>
