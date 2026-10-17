# Этап 1. Познакомиться с возможностями SimPy

<!-- TOC -->
- [Формальная задача этапа](#формальная-задача-этапа)
- [Что рабочая группа должна освоить в SimPy](#что-рабочая-группа-должна-освоить-в-simpy)
  - [Основные обьекты](#основные-обьекты)
  - [Ресурсы совместного использования](#ресурсы-совместного-использования)
- [Упражнения](#упражнения)
- [Критерий завершения этапа 1](#критерий-завершения-этапа-1)
- [Ссылки](#ссылки)
  - [SimPy intro courses](#simpy-intro-courses)
  - [SimPy documentation](#simpy-documentation)
<!-- /TOC -->

## Формальная задача этапа

Научиться представлять систему как набор взаимодействующих дискретных процессов и событий:

$$
S(t) \xrightarrow{\text{event}} S(t')
$$

Время в модели не меняется маленькими шагами. SimPy перескакивает от одного значимого события к следующему. Например:

| время | события |
| ------------------ | ---------------------------------- |
| $t = 12.4$ | автомобиль появился; |
| $t = 18.7$ | автомобиль доехал до перекрёстка и встал в очередь; |
| $t = 23.0$ | загорелся зелёный; |
| $t = 24.8$ | автомобиль покинул очередь; |
| $t = 37.1$ | автомобиль достиг следующего перекрёстка. |

**Материалы:** [DataCamp - Introduction to DES concepts](https://projector-video-pdf-converter.datacamp.com/30360/chapter1.pdf)

## Что рабочая группа должна освоить в SimPy

### Основные обьекты

| SimPy обьект | что представляет |
| ------------------ | ---------------------------------- |
| `Process` | процессы представляют из себя активные обьекты. Например: автомобили, пешеходы, светофоры, посетители магазина, запросы в службу поддержки |
| `Event` | события. Процессы используют отправку и реакцию или обработку событий для того чтобы организовать взаимодействие друг с другом или с Environment. Примеры событий: поступил новый запрос с службу поддержки или загорелся красный свет светофора или автомобиль появился на вьезде в перекресток |
| `Timeout` | Event специального типа, представляет из себя событие "прошел определенный промежуток времени". Такие событие позволяют процессу засыпать на заданное время, потом просыпаться. Или будить другие процессы |
| `Environment`      | Environment это среда проведения экспериментов, которая отвечает за координацию работы процессов |

**Материалы:** 

1. [DataCamp — Developing DES using SimPy](https://projector.datacamp.com/?projector_key=course_59914_33250b672fa85f691135dd7cb41169c2), Processes, yield, events, timeouts
2. [SimPy - Basic Concepts](https://simpy.readthedocs.io/en/latest/simpy_intro/basic_concepts.html)

### Ресурсы совместного использования

Ресурсы совместного использования — ещё один способ моделировать взаимодействие процессов. В SimPy предусмотрены три категории ресурсов.

| Тип | Что моделирует | Основные операции | Пример |
| --- | --- | --- | --- |
| `Resource` | управляет доступом. Ограниченное число мест, которые процессы временно занимают | `request()` — занять; `release()` — освободить | Две заправочные колонки: одновременно обслуживаются два автомобиля |
| `Container` | управляет количеством. Запас однородного содержимого, выраженный числом | `put(n)` — добавить количество; `get(n)` — забрать | Бак с топливом: добавить 100 литров, забрать 20 |
| `Store` | Набор отдельных объектов | `put(obj)` — положить объект; `get()` — получить объект | Очередь автомобилей, у каждого есть ID, маршрут и время прибытия |

Во всех трёх случаях процесс может ждать через `yield`:
* у `Resource` — освобождения места, 
* у `Container` — достаточного количества или свободной ёмкости, 
* у `Store` — появления объекта или места для добавления. Обычный `Store` выдаёт объекты в порядке поступления — FIFO.

В проекте это можно применять так:

- **`Resource(capacity=1)`** — участок, через который одновременно проезжает один автомобиль. Автомобиль занимает его на время проезда, затем освобождает.
- **`Container(capacity=5, init=5)`** — пять свободных мест на участке. При резервировании выполняется `get(1)`, при выезде — `put(1)`. `level` показывает число свободных мест.
- **`Store()`** — очередь перед переходом. В ней хранятся сами объекты автомобилей или пешеходов; обслуживающий процесс забирает следующий через `car = yield queue.get()`.

У `Resource` значение `capacity` ограничивает число **одновременных пользователей**, а очередь ожидающих может быть длиннее. У `Container` хранится только количество; у `Store` сохраняются данные каждого объекта.

**Материалы:**

1. [DataCamp — Developing DES using SimPy](https://projector-video-pdf-converter.datacamp.com/30360/chapter2.pdf), разделы "Types of resources" и "Managing the scheduling of events"
2. [SimPy — Ресурсы совместного использования](https://simpy.readthedocs.io/en/stable/topical_guides/resources.html)

## Упражнения

- [1. Симуляция одного светофора](stage-1-simpy/exercise-01.md)
- [2. Автомобиль едет по дороге, пешеход подходит к переходу](stage-1-simpy/exercise-02.md)
- [3. Потоки автомобилей и пешеходов на одной улице](stage-1-simpy/exercise-03.md)
- [4. Очереди перед регулируемым пешеходным переходом](stage-1-simpy/exercise-04.md)
- [4а. Светофор с кнопкой вызова пешеходной фазы](stage-1-simpy/exercise-04a.md)

- [6. Затор за переходом блокирует проезд автомобилей](stage-1-simpy/exercise-06.md)
- [6а. Три последовательных участка с независимыми светофорами](stage-1-simpy/exercise-06a.md)
- [7. Три направления движения и согласованные светофоры](stage-1-simpy/exercise-07.md)
- [8. Модель дорожной сети с четырьмя входящими потоками](stage-1-simpy/exercise-08.md)

## Критерий завершения этапа 1

После завершения этапа знаний SimPy достаточно, чтобы переходить к транспортной модели.

## Ссылки

### SimPy intro courses

1. [DataCamp - DES in Python Course](https://www.datacamp.com/courses/discrete-event-simulation-in-python) - paid
    1. [Chapter 1. Introduction to DES concepts](https://projector-video-pdf-converter.datacamp.com/30360/chapter1.pdf)
    2. [Chapter 2. Developing DES using SimPy](https://projector-video-pdf-converter.datacamp.com/30360/chapter2.pdf)
    3. [Chapter 3. Modelling DES](https://projector-video-pdf-converter.datacamp.com/30360/chapter3.pdf)
2. [HSMA - Introduction to DES Concepts](https://des.hsma.co.uk/intro_to_des_concepts.html)
3. [Building Healthcare DES models - An Introductory Tutorial](https://pythonhealthdatascience.github.io/intro-open-sim/lab/index.html)
4. [Chemical Process Control Course - Chapter 7. Introduction to SimPy](https://jckantor.github.io/cbe30338-2021/07.01-Introduction-to-Simpy.html)

### SimPy documentation

1. https://simpy.readthedocs.io/en/latest/topical_guides/environments.html
2. https://simpy.readthedocs.io/en/latest/topical_guides/process_interaction.html
2. https://simpy.readthedocs.io/en/latest/topical_guides/events.html
3. https://simpy.readthedocs.io/en/latest/topical_guides/resources.html
4. https://simpy.readthedocs.io/en/stable/topical_guides/monitoring.html
