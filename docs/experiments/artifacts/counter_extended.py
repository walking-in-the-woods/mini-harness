class Counter:
    """Counter that increments or decrements by a fixed step."""

    def __init__(self, start=0, step=1):
        self.value = start
        self.step = step
        self.history = [start]

    def get_value(self):
        return self.value

    def reset(self):
        self.value = 0
        self.history = [0]

    def get_history(self):
        return list(self.history)

    def increment(self):
        """Increments the counter value by the step and adds it to the history."""
        self.value += self.step
        self.history.append(self.value)

    def decrement(self):
        """Decrements the counter value by the step and adds it to the history."""
        self.value -= self.step
        self.history.append(self.value)