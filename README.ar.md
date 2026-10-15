<div dir="rtl" markdown="1">

# OrchestratorX — أوركستريتور إكس

**تنفيذ مرجعي بمشرف LangGraph لمراجعة محافظ مالية، مع توجيه مستقل عن النموذج وحالة مُنمّطة وأثر تنفيذ مرتب.**

يفصل المشروع التوجيه وسياسة التصعيد عن السرد الذي يولّده النموذج، لاختبار ترتيب
المتخصصين والتخفيف وإعادة المحاولة ومعالجة الإخفاقات. يعود RiskModeler وComplianceChecker
وClientAdvisor إلى المشرف، أما Reporter فهو العقدة النهائية.
يعمل العرض الافتراضي ومجموعة الاختبارات الـ41 دون مفاتيح API أو اتصال بمزوّد نموذج.

**للتحقق بعد تثبيت اعتماديات التطوير أدناه:**

```bash
.venv/Scripts/python -m orchestratorx.cli demo
.venv/Scripts/python -m pytest
```

**الحدود:** بيانات اصطناعية وقواعد توضيحية؛ لا مصادقة ولا تخزين دائم للأثر ولا بث.
حدود القفزات والمحاولات لا تفرض مهلة على استدعاء عالق. افحص أخطاء التشغيل والأثر
إلى جانب التقرير؛ لا يعرض Reporter أخطاء المتخصصين صراحة في نص التقرير.

*[English](./README.md) · [المواصفة](./SPEC.md)*

---

## الفكرة

عندما يُفوّض التوجيه إلى نموذج عبر موجّه نصي، يعتمد اختبار ترتيب التنفيذ ومعالجة الإخفاقات
على سلوك النموذج أيضًا. تجعل سياسة التوجيه الصريحة هذه القرارات قابلة للاختبار بصورة مستقلة.

يُخرج OrchestratorX التوجيه من الموجّه النصّي ويضعه في حالة مُنمّطة:

```python
def next_hop(state, breaker=None) -> str:
    if state.get("halted"):
        return REPORTER
    for agent in REQUIRED_SPECIALISTS:
        if agent not in state["visited"] and (breaker is None or not breaker.is_open(agent)):
            return agent
    ...
```

ولأنّ هذه الدالة لا تستشير أيّ نموذج، يمكن اختبار ترتيب المسار السليم دون اتصال:

```python
def test_compliance_checker_is_never_skipped_on_the_happy_path():
    ...
    assert seen.index("ComplianceChecker") < seen.index(REPORTER)
```

ويبقى النموذج اللغوي مسؤولًا عن صياغة السرد الموجّه للعميل، لكنّه لا يقرّر أبدًا ما إذا كانت
المحفظة ملتزمة.

---

## البدء السريع

```bash
git clone https://github.com/asadullah48/orchestratorx && cd orchestratorx
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m orchestratorx.cli demo
```

مخرجات حقيقية:

```
[1] Compliant book
    path       : supervisor -> RiskModeler -> supervisor -> ComplianceChecker -> supervisor -> Reporter
    escalation : continue  |  halted: False
    findings   : none

[2] Concentrated and over-levered
    path       : supervisor -> RiskModeler -> supervisor -> ComplianceChecker -> supervisor -> ClientAdvisor -> supervisor -> Reporter
    escalation : continue  |  halted: False
    findings   : RSK-001(medium), CMP-002(high), CMP-002(high), CMP-004(high)

[3] Restricted sector held
    path       : supervisor -> RiskModeler -> supervisor -> ComplianceChecker -> supervisor -> Reporter
    escalation : halt  |  halted: True
    findings   : CMP-003(critical)
```

لاحظ التشغيل الثاني: وجّهت النتائج عالية الخطورة المسار إلى `ClientAdvisor` **مرّة واحدة
فقط**، ثم أعاد العدّاد المحدود التصعيد إلى `continue` فانتهى التشغيل. أمّا التشغيل الثالث فلم
يبلغ المستشار إطلاقًا، إذ إنّ المخالفة الحرجة توقف التشغيل بدل البحث عن تخفيف لها.

---

## الرسم البياني

```mermaid
flowchart TD
    START([run]) --> S[supervisor]
    S -->|next_agent| RM[RiskModeler]
    S -->|next_agent| CC[ComplianceChecker]
    S -->|escalation = mitigate| CA[ClientAdvisor]
    S -->|required done| RP[Reporter]
    RM --> S
    CC --> S
    CA --> S
    RP --> DONE([report])
```

| الوكيل | مهمّته |
|---|---|
| **supervisor** | العقدة الوحيدة التي توجّه. تقرأ الحالة المُنمّطة وتختار القفزة التالية وتفرض الانتقالات المشروعة. |
| **RiskModeler** | يُقدّر التعرّض: التركّز، ومزيج القطاعات، والتقلّب المعدّل بالرافعة. يُبلّغ بالأرقام لا بالأحكام. |
| **ComplianceChecker** | يطبّق قواعد التفويض. وهنا تُصبح درجة الخطورة ذات أثر فعلي. |
| **ClientAdvisor** | يقترح إجراءات التخفيف، ولا يُبلَغ إلّا حين يطلب التصعيد ذلك. ولا يُسقط أيّ نتيجة بسلطته الذاتية. |
| **Reporter** | العقدة النهائية. يجمع المُخرَج، أو يوضّح سبب حجبه. |

يعود RiskModeler وComplianceChecker وClientAdvisor إلى المشرف فقط؛ أما Reporter فنهائي.
يُختبر ترتيب المتخصصين المطلوبين في المسار السليم، وقد يتجاوز التوقف أو القاطع المفتوح أحدهم.

---

## ثلاثة أمور تُمسك البنية معًا

**المُخفِّضات، لأنّ الرسم البياني يدور.** المسار `supervisor → specialist → supervisor` يعيد
زيارة العقد. تستخدم القوائم التراكمية للنتائج والعقد المزارة `Annotated[list, operator.add]`
لجمع التحديثات، بينما تُحدّث الحقول المفردة بالاستبدال.

**ميزانية قفزات، لا مجرّد سقف لإعادة المحاولة.** إعادة المحاولة تحدّ من إخفاقات عقدة واحدة،
أمّا **الدورة** فلا يحدّها إلّا ميزانية. وإلّا فإنّ "العودة للتخفيف ثم إعادة الفحص" حلقة لا
نهائية مشروعة لا يُسيء أيّ مكوّن التصرّف لإحداثها.

**عزل لكل تشغيل.** يُبحث عن الأثر والقاطع بمعرّف `run_id`، وتُحفظ القواطع في قاموس على
كائن المنسّق مفهرس بهذا المعرّف. تختبر المجموعة استقلال النتائج ومعرّفات التشغيل والآثار،
لكنها لا تحقن إخفاقًا في قاطع أحد التشغيلات المتزامنة لإثبات عزله عن البقية.

---

## سياسة التصعيد

المتخصّصون يُبلّغون بالوقائع، وما يفعله المشرف حيالها موقف من المخاطر، معزول في دالة واحدة
موسومة بـ `=== ESCALATION SEAM ===` في `policy.py`:

| أخطر نتيجة | الإجراء | السبب |
|---|---|---|
| `critical` | **إيقاف** | التقرير المبني على مخالفة حرجة أسوأ من غياب التقرير، لأنّه يبدو ذا حجّية. |
| `high` | **تخفيف** مرّة واحدة | اقتراح من ClientAdvisor ثم إعادة تقييم التصعيد على النتائج المحفوظة، دون إعادة فحوص المجال على محفظة معدّلة. |
| `medium` فأقلّ | **متابعة** | التوثيق ثم إصدار التقرير. |

تتركز قرارات التصعيد في `decide_escalation()` عند `=== ESCALATION SEAM ===`.
يتطلب تغيير عدد جولات التخفيف مراجعة `MAX_MITIGATION_ROUNDS` وميزانية القفزات أيضًا.
الاقتراحات لا تمحو النتائج الأصلية.

---

## الموثوقية

| الإخفاق | السلوك |
|---|---|
| خطأ عابر في متخصّص | يُعاد حتى `max_attempts`، ويكتمل التشغيل ويُسجَّل عدد المحاولات |
| نفاد محاولات المتخصص | يُسجّل الخطأ في الحالة والأثر، ويزداد عداد إخفاق القاطع حتى يبلغ عتبة الفتح |
| انتقال غير مشروع | `PolicyViolationError`، **ولا يُعاد أبدًا** لأنّ إعادة خطأ حتمي تُعيد إنتاجه |
| دورة جامحة | `BudgetExceededError` عند `MAX_HOPS`، وينتهي التشغيل عند المُبلِّغ |

تحدّ هذه الآليات من خطوات التنفيذ وعدد المحاولات، لكنها لا تفرض مهلة على استدعاء عالق.
تُحفظ أخطاء المتخصصين المعالَجة في الحالة والأثر، ولا يعرضها Reporter صراحة في نص التقرير.
لذلك يجب فحص الأخطاء والأثر إلى جانب التقرير.

---

## واجهة HTTP

```bash
pip install -e ".[api]"
uvicorn orchestratorx.api:app --reload
```

| المسار | الغرض |
|---|---|
| `GET /health` | الإصدار والمُستدِل النشط والسياسة السارية |
| `POST /run` | تشغيل تنسيق وإرجاع النتائج والتقرير |
| `GET /trace/{run_id}` | استرجاع ما فعله ذلك التشغيل بالضبط، قفزةً قفزة |

والمسار الثالث هو بيت القصيد: المنسّق الذي لا يمكن استجوابه لاحقًا ليس قابلًا للتدقيق مهما
كان تسجيله جيّدًا.

---

## التطوير

```bash
.venv/Scripts/python -m pytest      # 41 اختبارًا
.venv/Scripts/python -m ruff check .
```

```
src/orchestratorx/
  state.py         الحالة المُنمّطة والمُخفِّضات وFinding / AgentResult
  policy.py        التوجيه وإعادة المحاولة وقاطع الدارة ودرز التصعيد
  agents.py        RiskModeler و ComplianceChecker و ClientAdvisor و Reporter
  orchestrator.py  عقدة المشرف وتركيب الرسم البياني
  trace.py         أثر التدقيق المُرتّب
  llm.py           FakeLLM افتراضيًّا، مع Claude / Gemini اختياريًّا
  api.py           خدمة FastAPI
  cli.py           نقطة دخول سطر الأوامر
```

---

## الحالة والحدود

تنفيذ مرجعي، وحدوده مقصودة: لا مصادقة، ولا تخزين دائم (سجلّ الأثر داخل العملية، و`write_json`
هو درز التصدير)، ولا بثّ، وقواعد المجال تفويض معقول لا تفويض حقيقي. وكل بيانات العملاء
والمُصدرين والسوق اصطناعية. لا يمثل هذا دليلًا على تشغيل لدى عميل في الإنتاج،
والأثر سجل تنفيذ داخل العملية وليس نظام تدقيق دائمًا.

## الرخصة

MIT

</div>
