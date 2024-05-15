instruction = """
<p><strong>I Регистрация в сервисе </strong><strong>asu-</strong><strong>payme.</strong><strong>com</strong></p>
<ol>
<li>Предоставить ваш email. Дождаться внесения в базу.</li>
<li>Зарегистрироваться <a href="https://asu-payme.com/auth/signup/">https://asu-payme.com/auth/signup/</a></li>
<li>Авторизоваться в системе <a href="https://asu-payme.com/auth/login/">https://asu-payme.com/auth/login/</a></li>
<li>Создать ваш магазин Merchant и заполнить данные:</li>
</ol>
<p>- name<sup>*</sup></p>
<p>- endpoint<sup>*</sup> для отправки webhook о подтверждении оплаты</p>
<p>- secret_key<sup>*</sup></p>
<p>- url для возврата пользователя после подтверждения платежа</p>
<p>&nbsp;</p>
<p>Вашему магазину будет присвоен merchant_id</p>
<p>&nbsp;</p>
<p><strong>II Проведение оплаты.</strong></p>
<p><strong>Вариант оплаты 1</strong><strong>: .</strong></p>
<p>Перенаправить пользователя на страницу <a href="https://asu-payme.com/invoice">https://asu-payme.com/invoice</a>/ c аргументами:</p>
<p>&nbsp;</p>
<table>
<tbody>
<tr>
<td>
<p><strong>arg</strong></p>
</td>
<td>
<p><strong>type</strong></p>
</td>
<td>
<p><strong>description</strong></p>
</td>
</tr>
<tr>
<td>
<p>merchant_id<sup>*</sup></p>
</td>
<td>
<p>Integer</p>
</td>
<td>
<p>id мерчанта</p>
</td>
</tr>
<tr>
<td>
<p>order_id<sup>*</sup></p>
</td>
<td>
<p>String(36)</p>
</td>
<td>
<p>идентификатор заказа</p>
</td>
</tr>
<tr>
<td>
<p>owner_name</p>
</td>
<td>
<p>String(100)</p>
</td>
<td>
<p>имя плательщика</p>
</td>
</tr>
<tr>
<td>
<p>user_login</p>
</td>
<td>
<p>String(36)</p>
</td>
<td>
<p>идентификатор пользователя</p>
</td>
</tr>
<tr>
<td>
<p>amount</p>
</td>
<td>
<p>Integer</p>
</td>
<td>
<p>сумма</p>
</td>
</tr>
<tr>
<td>
<p>pay_type<sup>*</sup></p>
</td>
<td>
<p>&ldquo;card-to-m10&rdquo;</p>
</td>
<td>
<p>тип платежа</p>
</td>
</tr>
<tr>
<td>
<p>signature<sup>*</sup></p>
</td>
<td>
<p>String()</p>
</td>
<td>
<p>сигнатура</p>
</td>
</tr>
</tbody>
</table>
<p>&nbsp;</p>
<p>Пример:</p>
<p><a href="https://asu-payme.com/invoice/">https://asu-payme.com/invoice/</a></p>
<p>?merchant_id=1</p>
<p>&amp;order_id=xxxx-yyyy-zzz-12334</p>
<p>&amp;amount=5</p>
<p>&amp;owner_name=John%20Dou</p>
<p>&amp;user_login=user_22216456</p>
<p>&amp;pay_type=card-to-m10</p>
<p>&amp;signature= 1a7a8735e137b2286f011b9b209839c5145a687b9a99a3f1c1a8810d5fd2164d</p>
<p>&nbsp;</p>
<p>Расчет <span>signature:</span></p>
<p>string = merchant_id + order_id + secret_key; (encoding UTF-8)</p>
<p><em>signature</em><em> = hash('sha256', $string)</em></p>
<p>В примере <span>string = </span>1xxxx-yyyy-zzz-12334secret_key</p>
<p>&nbsp;</p>
<p>Далее пользователь действует по инструкции сайта. После подтверждения платежа на endpoint указанный при регистрации отправляется POST-запрос:</p>
<p>&nbsp;</p>
<p>POST endpoint</p>
<p>Content-Type: application/json</p>
<p>&nbsp;</p>
<p>{</p>
<p>&nbsp; "id": "65dba7ee-2e8a-46fd-88f2-9855fed36a39",&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; / идентификатор asu-payme</p>
<p>&nbsp; "order_id": "order_id",</p>
<p>&nbsp; "user_login": "user_login",</p>
<p>&nbsp; "amount": 500,</p>
<p>&nbsp; "create_at": 1712754413.239696, &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; / unix timestamp</p>
<p>&nbsp; "status": 9, &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; / -1 отклонен, 9 подтвержден</p>
<p>&nbsp; "confirmed_time": 1712754513.231124, &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; / unix timestamp</p>
<p>&nbsp; "confirmed_amount": 400,&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; / подтвержденная сумма</p>
<p>&nbsp; "signature": "3836538f6c44a7f2864939728528739a952ec1cf03f27234153c9ca6743a5562"</p>
<p>}</p>
<p>&nbsp;</p>
<p>Расчет signature<span>:</span></p>
<p>string = merchant_id + id + confirmed_amount + secret_key; (encoding UTF-8)</p>
<p><em>signature</em><em> = hash('sha256', $string)</em></p>
<p>В примере <span>string = </span>165dba7ee-2e8a-46fd-88f2-9855fed36a39400secret_key</p>
<p>&nbsp;</p>
<p><strong>Вариант 2. Воспользоваться нашим </strong><strong>API:</strong></p>
<p>&nbsp;</p>
"""