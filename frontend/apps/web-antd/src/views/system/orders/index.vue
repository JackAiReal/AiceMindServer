<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';

import {
  cancelOrderApi,
  createOrderApi,
  initiatePaymentApi,
  listAccountsApi,
  listOrderRefundsApi,
  listOrdersApi,
  listOrderStateEventsApi,
  listPlansApi,
  markOrderExceptionApi,
  markOrderPaidApi,
  recoverOrderApi,
  refundOrderApi,
  type AccountItem,
  type OrderItem,
  type OrderRefundItem,
  type OrderStateEventItem,
  type PlanItem,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  message,
  Modal,
  Modal as AModal,
  Select as ASelect,
  Space as ASpace,
  Table as ATable,
  Tag as ATag,
} from 'ant-design-vue';

type OrderStatus = 'created' | 'paid' | 'cancelled';

interface OrderFormState {
  accountId: string;
  planCode: string;
  amount: number;
  currency: string;
  channel: string;
  status: OrderStatus;
  note: string;
}

interface RefundFormState {
  orderId: string;
  amount?: number;
  provider: string;
  reason: string;
  externalRefundNo: string;
}

const loading = ref(false);
const submitting = ref(false);
const createOpen = ref(false);
const payOpen = ref(false);
const paySubmitting = ref(false);
const payProvider = ref<'alipay' | 'wechat'>('alipay');
const payPreview = ref('');
const currentPayOrder = ref<OrderItem | null>(null);

const refundOpen = ref(false);
const refundSubmitting = ref(false);
const refundForm = reactive<RefundFormState>({
  orderId: '',
  amount: undefined,
  provider: 'manual',
  reason: '',
  externalRefundNo: '',
});

const traceLoading = ref(false);
const selectedOrderId = ref('');
const refunds = ref<OrderRefundItem[]>([]);
const stateEvents = ref<OrderStateEventItem[]>([]);

const orders = ref<OrderItem[]>([]);
const accounts = ref<AccountItem[]>([]);
const plans = ref<PlanItem[]>([]);

const orderForm = reactive<OrderFormState>({
  accountId: '',
  planCode: '',
  amount: 0,
  currency: 'CNY',
  channel: 'manual',
  status: 'created',
  note: '',
});

const accountOptions = computed(() =>
  accounts.value.map((item) => ({
    label: `${item.username || '-'} (${item.email || item.id})`,
    value: item.id,
  })),
);

const planOptions = computed(() =>
  plans.value.map((item) => ({
    label: `${item.name} (${item.code})`,
    value: item.code,
  })),
);

const currencyOptions = [
  { label: 'CNY', value: 'CNY' },
  { label: 'USD', value: 'USD' },
  { label: 'HKD', value: 'HKD' },
];

const channelOptions = [
  { label: 'Manual（线下/人工）', value: 'manual' },
  { label: 'Alipay', value: 'alipay' },
  { label: 'WeChat Pay', value: 'wechat' },
  { label: 'Stripe', value: 'stripe' },
  { label: 'Apple IAP', value: 'apple' },
  { label: 'Google Play', value: 'google' },
];

const statusOptions = [
  { label: '待支付', value: 'created' },
  { label: '已支付', value: 'paid' },
  { label: '已取消', value: 'cancelled' },
];

const payProviderOptions = [
  { label: '支付宝', value: 'alipay' },
  { label: '微信支付', value: 'wechat' },
];

const refundProviderOptions = [
  { label: '人工退款', value: 'manual' },
  { label: '支付宝', value: 'alipay' },
  { label: '微信支付', value: 'wechat' },
  { label: 'Stripe', value: 'stripe' },
];

const resetOrderForm = () => {
  orderForm.accountId = '';
  orderForm.planCode = '';
  orderForm.amount = 0;
  orderForm.currency = 'CNY';
  orderForm.channel = 'manual';
  orderForm.status = 'created';
  orderForm.note = '';
};

const loadTrace = async () => {
  if (!selectedOrderId.value) {
    refunds.value = [];
    stateEvents.value = [];
    return;
  }
  traceLoading.value = true;
  try {
    const [refundRows, eventRows] = await Promise.all([
      listOrderRefundsApi({ orderId: selectedOrderId.value, limit: 100 }),
      listOrderStateEventsApi({ orderId: selectedOrderId.value, limit: 100 }),
    ]);
    refunds.value = refundRows || [];
    stateEvents.value = eventRows || [];
  } finally {
    traceLoading.value = false;
  }
};

const loadData = async () => {
  loading.value = true;
  try {
    const [orderRows, accountRows, planRows] = await Promise.all([
      listOrdersApi(),
      listAccountsApi(),
      listPlansApi(),
    ]);
    orders.value = orderRows || [];
    accounts.value = accountRows || [];
    plans.value = planRows || [];
    if (!selectedOrderId.value && orders.value.length > 0) {
      const firstOrder = orders.value[0];
      if (firstOrder) {
        selectedOrderId.value = firstOrder.id;
      }
    }
    await loadTrace();
  } finally {
    loading.value = false;
  }
};

const onSelectPlan = (value: any) => {
  const planCode = String(value || '').trim();
  const selectedPlan = plans.value.find((item) => item.code === planCode);
  if (selectedPlan && Number(orderForm.amount || 0) <= 0) {
    orderForm.amount = Number(selectedPlan.price || 0);
  }
};

const onOpenCreate = () => {
  resetOrderForm();
  createOpen.value = true;
};

const onCreateOrder = async () => {
  if (!orderForm.accountId || !orderForm.planCode) {
    message.error('账号与套餐必填');
    return;
  }
  if (orderForm.amount < 0) {
    message.error('金额不能为负数');
    return;
  }

  submitting.value = true;
  try {
    await createOrderApi({
      accountId: orderForm.accountId,
      planCode: orderForm.planCode,
      amount: Number(orderForm.amount || 0),
      currency: orderForm.currency,
      channel: orderForm.channel,
      status: orderForm.status,
      note: orderForm.note.trim(),
    });
    message.success('订单已创建');
    createOpen.value = false;
    await loadData();
  } finally {
    submitting.value = false;
  }
};

const onMarkPaid = (row: OrderItem) => {
  Modal.confirm({
    title: '确认标记为已支付？',
    content: `${row.orderNo} / ${row.planName || row.planCode}`,
    okText: '确认',
    cancelText: '取消',
    async onOk() {
      await markOrderPaidApi(row.id);
      message.success('订单已标记为已支付');
      selectedOrderId.value = row.id;
      await loadData();
    },
  });
};

const onCancelOrder = (row: OrderItem) => {
  Modal.confirm({
    title: '确认取消订单？',
    content: row.orderNo,
    okType: 'danger',
    async onOk() {
      await cancelOrderApi({ orderId: row.id, reason: 'admin cancel' });
      message.success('订单已取消');
      selectedOrderId.value = row.id;
      await loadData();
    },
  });
};

const onMarkException = (row: OrderItem) => {
  Modal.confirm({
    title: '确认标记为异常订单？',
    content: row.orderNo,
    okType: 'danger',
    async onOk() {
      await markOrderExceptionApi({ orderId: row.id, reason: 'manual risk review' });
      message.success('已标记异常订单');
      selectedOrderId.value = row.id;
      await loadData();
    },
  });
};

const onRecoverOrder = (row: OrderItem) => {
  Modal.confirm({
    title: '确认恢复该异常订单？',
    content: row.orderNo,
    async onOk() {
      await recoverOrderApi({ orderId: row.id, reason: 'manual recover' });
      message.success('异常订单已恢复');
      selectedOrderId.value = row.id;
      await loadData();
    },
  });
};

const onOpenRefund = (row: OrderItem) => {
  refundForm.orderId = row.id;
  refundForm.amount = undefined;
  refundForm.provider = row.channel || 'manual';
  refundForm.reason = '';
  refundForm.externalRefundNo = '';
  refundOpen.value = true;
};

const onSubmitRefund = async () => {
  if (!refundForm.orderId) {
    message.error('缺少订单ID');
    return;
  }
  refundSubmitting.value = true;
  try {
    await refundOrderApi({
      orderId: refundForm.orderId,
      amount: refundForm.amount,
      provider: refundForm.provider,
      reason: refundForm.reason.trim(),
      externalRefundNo: refundForm.externalRefundNo.trim(),
    });
    message.success('退款处理成功');
    refundOpen.value = false;
    selectedOrderId.value = refundForm.orderId;
    await loadData();
  } finally {
    refundSubmitting.value = false;
  }
};

const onOpenInitiatePay = (row: OrderItem) => {
  currentPayOrder.value = row;
  payProvider.value = row.channel === 'wechat' ? 'wechat' : 'alipay';
  payPreview.value = '';
  payOpen.value = true;
};

const onInitiatePay = async () => {
  if (!currentPayOrder.value) {
    message.error('缺少订单信息');
    return;
  }

  paySubmitting.value = true;
  try {
    const result = await initiatePaymentApi({
      orderId: currentPayOrder.value.id,
      provider: payProvider.value,
    });
    payPreview.value = JSON.stringify(result || {}, null, 2);
    message.success('支付请求已生成，请按网关参数发起支付');
    selectedOrderId.value = currentPayOrder.value.id;
    await loadData();
  } finally {
    paySubmitting.value = false;
  }
};

onMounted(() => {
  void loadData();
});
</script>

<template>
  <div class="orders-page">
    <ASpace direction="vertical" :size="16" style="width: 100%">
      <ACard title="订单管理" :bordered="false">
        <template #extra>
          <ASpace>
            <AButton @click="loadData">刷新</AButton>
            <AButton type="primary" @click="onOpenCreate">创建订单</AButton>
          </ASpace>
        </template>

        <ATable :data-source="orders" :loading="loading" row-key="id" :pagination="{ pageSize: 10 }">
          <ATable.Column title="订单号" data-index="orderNo" key="orderNo" width="200" />
          <ATable.Column title="用户" key="account" width="220">
            <template #default="{ record }">
              <div>{{ record.username || '-' }}</div>
              <div style="font-size: 12px; color: #888">{{ record.email || record.accountId }}</div>
            </template>
          </ATable.Column>
          <ATable.Column title="套餐" key="plan" width="220">
            <template #default="{ record }">
              <div>{{ record.planName || record.planCode }}</div>
              <div style="font-size: 12px; color: #888">{{ record.planCode }}</div>
            </template>
          </ATable.Column>
          <ATable.Column title="金额" key="amount" width="180">
            <template #default="{ record }">
              <div>{{ record.amount }} {{ record.currency }}</div>
              <div style="font-size: 12px; color: #888">
                已退 {{ record.refundedAmount || 0 }} / 可退 {{ record.refundableAmount || 0 }}
              </div>
            </template>
          </ATable.Column>
          <ATable.Column title="渠道" data-index="channel" key="channel" width="120" />
          <ATable.Column title="状态" key="status" width="120">
            <template #default="{ record }">
              <ATag
                :color="
                  record.status === 'paid'
                    ? 'green'
                    : record.status === 'exception'
                      ? 'red'
                      : record.status === 'refund_partial'
                        ? 'orange'
                        : record.status === 'refunded'
                          ? 'purple'
                          : record.status === 'cancelled'
                            ? 'default'
                            : 'blue'
                "
              >
                {{ record.status }}
              </ATag>
            </template>
          </ATable.Column>
          <ATable.Column title="支付时间" data-index="paidAt" key="paidAt" width="180" />
          <ATable.Column title="创建时间" data-index="createdAt" key="createdAt" width="180" />
          <ATable.Column title="操作" key="actions" width="360">
            <template #default="{ record }">
              <ASpace wrap>
                <AButton type="link" size="small" @click="selectedOrderId = record.id; loadTrace()">追溯</AButton>
                <AButton
                  type="link"
                  size="small"
                  :disabled="record.status !== 'created'"
                  @click="onOpenInitiatePay(record)"
                >
                  发起支付
                </AButton>
                <AButton
                  type="link"
                  size="small"
                  :disabled="record.status !== 'created' && record.status !== 'exception'"
                  @click="onMarkPaid(record)"
                >
                  标记已支付
                </AButton>
                <AButton
                  type="link"
                  size="small"
                  :disabled="record.status !== 'created'"
                  @click="onCancelOrder(record)"
                >
                  取消
                </AButton>
                <AButton
                  danger
                  type="link"
                  size="small"
                  :disabled="record.status === 'cancelled' || record.status === 'refunded'"
                  @click="onMarkException(record)"
                >
                  标记异常
                </AButton>
                <AButton type="link" size="small" :disabled="record.status !== 'exception'" @click="onRecoverOrder(record)">
                  恢复
                </AButton>
                <AButton
                  type="link"
                  size="small"
                  :disabled="record.status !== 'paid' && record.status !== 'refund_partial'"
                  @click="onOpenRefund(record)"
                >
                  退款
                </AButton>
              </ASpace>
            </template>
          </ATable.Column>
        </ATable>
      </ACard>

      <ASpace :size="16" style="width: 100%; align-items: flex-start" wrap>
        <ACard title="退款记录" :bordered="false" style="flex: 1; min-width: 420px">
          <template #extra>
            <ASpace>
              <span style="font-size: 12px; color: #888">订单: {{ selectedOrderId || '-' }}</span>
              <AButton size="small" @click="loadTrace">刷新</AButton>
            </ASpace>
          </template>
          <ATable :data-source="refunds" :loading="traceLoading" row-key="id" :pagination="{ pageSize: 5 }" size="small">
            <ATable.Column title="时间" data-index="createdAt" key="createdAt" width="160" />
            <ATable.Column title="金额" key="amount" width="120">
              <template #default="{ record }">{{ record.amount }} {{ record.currency }}</template>
            </ATable.Column>
            <ATable.Column title="渠道" data-index="provider" key="provider" width="90" />
            <ATable.Column title="状态" data-index="status" key="status" width="90" />
            <ATable.Column title="原因" data-index="reason" key="reason" />
          </ATable>
        </ACard>

        <ACard title="订单状态流转" :bordered="false" style="flex: 1; min-width: 420px">
          <template #extra>
            <ASpace>
              <span style="font-size: 12px; color: #888">订单: {{ selectedOrderId || '-' }}</span>
              <AButton size="small" @click="loadTrace">刷新</AButton>
            </ASpace>
          </template>
          <ATable
            :data-source="stateEvents"
            :loading="traceLoading"
            row-key="id"
            :pagination="{ pageSize: 5 }"
            size="small"
          >
            <ATable.Column title="时间" data-index="createdAt" key="createdAt" width="160" />
            <ATable.Column title="流转" key="flow" width="130">
              <template #default="{ record }">{{ record.fromStatus || '-' }} → {{ record.toStatus }}</template>
            </ATable.Column>
            <ATable.Column title="操作者" key="actor" width="130">
              <template #default="{ record }">{{ record.actorUsername || record.actorAccountId || '-' }}</template>
            </ATable.Column>
            <ATable.Column title="来源" data-index="source" key="source" width="160" />
            <ATable.Column title="原因" data-index="reason" key="reason" />
          </ATable>
        </ACard>
      </ASpace>
    </ASpace>

    <AModal
      v-model:open="createOpen"
      title="创建订单"
      :confirm-loading="submitting"
      ok-text="创建"
      cancel-text="取消"
      @ok="onCreateOrder"
    >
      <AForm layout="vertical">
        <AFormItem label="账号" required>
          <ASelect
            v-model:value="orderForm.accountId"
            :options="accountOptions"
            show-search
            :filter-option="(input, option) => String(option?.label || '').toLowerCase().includes(input.toLowerCase())"
            placeholder="请选择账号"
          />
        </AFormItem>
        <AFormItem label="套餐" required>
          <ASelect
            v-model:value="orderForm.planCode"
            :options="planOptions"
            placeholder="请选择套餐"
            @change="onSelectPlan"
          />
        </AFormItem>
        <AFormItem label="金额" required>
          <AInputNumber v-model:value="orderForm.amount" :min="0" :precision="2" style="width: 100%" />
        </AFormItem>
        <AFormItem label="币种">
          <ASelect v-model:value="orderForm.currency" :options="currencyOptions" />
        </AFormItem>
        <AFormItem label="渠道">
          <ASelect v-model:value="orderForm.channel" :options="channelOptions" />
        </AFormItem>
        <AFormItem label="订单状态">
          <ASelect v-model:value="orderForm.status" :options="statusOptions" />
        </AFormItem>
        <AFormItem label="备注">
          <AInput.TextArea v-model:value="orderForm.note" :rows="3" placeholder="可记录交易备注" />
        </AFormItem>
      </AForm>
    </AModal>

    <AModal
      v-model:open="payOpen"
      title="发起支付"
      :confirm-loading="paySubmitting"
      ok-text="生成支付请求"
      cancel-text="关闭"
      @ok="onInitiatePay"
    >
      <AForm layout="vertical">
        <AFormItem label="订单信息">
          <div>
            {{ currentPayOrder?.orderNo || '-' }} / {{ currentPayOrder?.planName || currentPayOrder?.planCode || '-' }}
          </div>
          <div style="font-size: 12px; color: #888">
            金额：{{ currentPayOrder?.amount || 0 }} {{ currentPayOrder?.currency || 'CNY' }}
          </div>
        </AFormItem>
        <AFormItem label="支付渠道" required>
          <ASelect v-model:value="payProvider" :options="payProviderOptions" />
        </AFormItem>
        <AFormItem label="支付请求参数预览">
          <AInput.TextArea
            v-model:value="payPreview"
            :rows="12"
            placeholder="点击“生成支付请求”后会返回网关参数和签名，用于第三方支付对接"
          />
        </AFormItem>
      </AForm>
    </AModal>

    <AModal
      v-model:open="refundOpen"
      title="退款处理"
      :confirm-loading="refundSubmitting"
      ok-text="确认退款"
      cancel-text="取消"
      @ok="onSubmitRefund"
    >
      <AForm layout="vertical">
        <AFormItem label="订单ID">
          <AInput :value="refundForm.orderId" disabled />
        </AFormItem>
        <AFormItem label="退款金额（留空默认可退全额）">
          <AInputNumber v-model:value="refundForm.amount" :min="0.01" :precision="2" style="width: 100%" />
        </AFormItem>
        <AFormItem label="退款渠道">
          <ASelect v-model:value="refundForm.provider" :options="refundProviderOptions" />
        </AFormItem>
        <AFormItem label="外部退款单号">
          <AInput v-model:value="refundForm.externalRefundNo" placeholder="可选" />
        </AFormItem>
        <AFormItem label="退款原因">
          <AInput.TextArea v-model:value="refundForm.reason" :rows="3" placeholder="可选" />
        </AFormItem>
      </AForm>
    </AModal>
  </div>
</template>

<style scoped>
.orders-page {
  padding: 16px;
}
</style>
